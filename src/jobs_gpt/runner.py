"""Fail-closed form runner; one persisted submit intent, then read-only recovery."""

import hashlib
import re
import time
from pathlib import Path

from . import forms
from .browser import page_for, target_id
from .gemini import request_answers
from .models import assert_job_url, eligibility_issues, job_key, now
from .reviews import approved, revalidate
from .state import STOP

LIMIT = re.compile(
    r"(?:reached|exceeded).{0,45}application limit|limiting applications|cannot submit.{0,100}(?:position|application)|may (?:only )?apply to.{0,40}(?:days|months)",
    re.I | re.S,
)


def visible_alerts(scope):
    elements = scope.locator(
        "[role=alert],.field-error,.error-message,[class*=errorMessage]"
    )
    return [
        elements.nth(i).inner_text()[:800]
        for i in range(min(elements.count(), 30))
        if elements.nth(i).is_visible()
    ]


def limit_notice(scope):
    return next((s for s in visible_alerts(scope) if LIMIT.search(s)), None)


def screenshot(page, store, key, label):
    path = (
        store.root
        / "screenshots"
        / (hashlib.sha256(key.encode()).hexdigest()[:16] + "-" + label + ".png")
    )
    path.parent.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True, timeout=7000)
        return str(path)
    except Exception:
        return None


def upload_verified(scope, profile):
    actions = []
    for f in forms.discover(scope):
        if f["kind"] != "file" or not f["value"]:
            continue
        if not re.search(
            r"\b(resum[eé]|cv|curriculum vitae)\b", f["label"], re.I
        ) or re.search(r"cover letter", f["label"], re.I):
            continue
        actual = forms.element(scope, f).evaluate("""async e => {
          if(e.files.length!==1)return null;
          return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await e.files[0].arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('');
        }""")
        if actual == profile["_resume_sha256"]:
            actions.append(
                {
                    "field": f["label"],
                    "field_id": f["id"],
                    "source": "resume",
                    "value": f["value"],
                }
            )
    return actions


def wait_quiet(page, pending, timeout=15):
    end = time.monotonic() + timeout
    quiet = None
    while time.monotonic() < end:
        if not pending:
            quiet = quiet or time.monotonic()
            if time.monotonic() - quiet >= 0.6:
                return True
        else:
            quiet = None
        page.wait_for_timeout(100)
    return False


def process(context, store, job, profile, bank, submit=False, model=None):
    key = job_key(job)
    old = store.get(key) or {}
    status = old.get("status")
    # Confirmed records remain final. Only close their recorded tab if a prior close failed.
    if status == "submitted":
        if old.get("target_id"):
            for page in context.pages:
                if target_id(context, page) == old["target_id"]:
                    assert_job_url(job, page.url)
                    page.close()
        return old
    page = page_for(context, old.get("target_id"))
    record = dict(old, job=job, target_id=target_id(context, page), url=job["url"])
    protected = status in STOP
    pending = set()

    def requested(req):
        if req.resource_type in ("fetch", "xhr"):
            pending.add(req)

    def finished(req):
        pending.discard(req)

    page.on("request", requested)
    page.on("requestfinished", finished)
    page.on("requestfailed", finished)
    try:
        if page.url == "about:blank":
            page.goto(job["url"], wait_until="domcontentloaded", timeout=35000)
        assert_job_url(job, page.url)
        # Never click Apply, edit fields or retry after a possibly received submission.
        scope = page.main_frame if protected else forms.open_application(page)
        assert_job_url(job, page.url)
        if scope != page.main_frame:
            assert_job_url(job, scope.url)
        record["current_url"] = page.url
        receipt = forms.confirmation(scope)
        buttons = forms.submit_buttons(scope)
        active = any(buttons.nth(i).is_visible() for i in range(buttons.count()))
        if protected:
            if status in ("submitting", "unconfirmed") and receipt and not active:
                record.update(
                    status="submitted",
                    confirmation=receipt[0],
                    screenshot=screenshot(page, store, key, "receipt"),
                )
                store.outcome(key, record, {"receipt": receipt[0], "url": page.url})
                page.close()
            else:
                # Preserve uncertainty and the tab; never interpret absence of a receipt as rejection.
                record.update(
                    status=status,
                    blockers=old.get("blockers")
                    or [
                        "Protected prior attempt: inspect employer receipt before any retry"
                    ],
                )
                store.put(key, record, "read_only_recovery")
            return record
        issues = forms.blockers(scope, [], reviewed=True)
        if any("CAPTCHA" in s for s in issues):
            record.update(
                status="captcha_blocked",
                blockers=["Interactive CAPTCHA: manual completion required; left open"],
            )
            return store.put(key, record)
        notice = limit_notice(scope)
        if notice:
            record.update(status="application_limit", blockers=[notice])
            return store.put(key, record)
        answers = approved(bank, profile, job)
        actions = []
        forms.fill(
            scope,
            profile["fields"],
            Path(profile["_resume_path"]),
            answers,
            job,
            actions,
        )
        if not wait_quiet(page, pending):
            issues = ["Uploads/network still active"]
        else:
            issues = []
        # Recompute trust from source-bound answers, including restored fields. Never use
        # old action IDs to bless edited values or hidden model content.
        trusted = revalidate(scope, profile, answers, job) + upload_verified(
            scope, profile
        )
        issues += forms.blockers(scope, trusted)
        issues += eligibility_issues(job, profile)
        for other in store.all().values():
            if (
                other.get("status") == "application_limit"
                and other.get("job", {}).get("company", "").casefold()
                == job["company"].casefold()
            ):
                issues.append(
                    "Employer application limit previously recorded; review before further submissions"
                )
        if model:
            candidates = [
                f
                for f in forms.discover(scope)
                if not f["value"]
                and f["kind"] in ("text", "textarea", "contenteditable")
            ]
            if candidates:
                try:
                    drafts = request_answers(
                        {k: v for k, v in profile.items() if not k.startswith("_")},
                        Path(profile["_resume_path"]),
                        job,
                        candidates,
                        scope.locator("body").inner_text(),
                        model,
                    )
                    record["drafts"] = [
                        dict(
                            d,
                            question=next(
                                (
                                    f["label"]
                                    for f in candidates
                                    if f["id"] == d.get("field_id")
                                ),
                                None,
                            ),
                        )
                        for d in drafts
                    ]
                    record["draft_profile_digest"] = profile["_profile_digest"]
                    record["draft_resume_sha256"] = profile["_resume_sha256"]
                    issues.append(
                        "Gemini drafts saved for source review; approve supported answers and rerun"
                    )
                except Exception as exc:
                    issues.append(
                        "Gemini drafting failed: "
                        + type(exc).__name__
                        + "; check model, quota and local key"
                    )
        record.update(
            status="needs_review" if issues else ("ready" if submit else "preview"),
            actions=actions,
            blockers=sorted(set(issues)),
            fields=forms.discover(scope),
            updated_at=now(),
        )
        store.put(key, record)
        if not submit or issues:
            return record
        # Recheck every gate immediately before intent. No final-button heuristics or AI clicks.
        assert_job_url(job, page.url)
        final = forms.blockers(
            scope,
            revalidate(scope, profile, answers, job) + upload_verified(scope, profile),
        ) + eligibility_issues(job, profile)
        visible = [
            buttons.nth(i)
            for i in range(buttons.count())
            if buttons.nth(i).is_visible()
        ]
        if final or len(visible) != 1 or not visible[0].is_enabled():
            record.update(
                status="needs_review",
                blockers=final + ["Final form/button checks did not pass"],
            )
            return store.put(key, record)
        before = forms.confirmation(scope)
        record["screenshot_before"] = screenshot(page, store, key, "before")
        record["submit_attempt_at"] = now()
        if not store.begin_attempt(key, record):
            return store.get(key)
        record["status"] = "submitting"
        try:
            visible[0].click(timeout=10000)
            receipt = forms.confirmation_after(scope, before)
            if receipt:
                record.update(
                    status="submitted",
                    confirmation=receipt,
                    screenshot=screenshot(page, store, key, "receipt"),
                    blockers=[],
                )
                store.outcome(key, record, {"receipt": receipt, "url": page.url})
                page.close()
                return record
            notice = limit_notice(scope)
            if notice:
                record.update(status="application_limit", blockers=[notice])
                return store.outcome(key, record, {"employer_limit": notice})
            issues = forms.blockers(
                scope,
                revalidate(scope, profile, answers, job)
                + upload_verified(scope, profile),
            )
            if any("CAPTCHA" in s for s in issues):
                record["status"] = "captcha_blocked"
            else:
                record["status"] = "unconfirmed"
            record["blockers"] = issues or [
                "No fresh employer receipt; leave open and reconcile before retrying"
            ]
            return store.outcome(
                key,
                record,
                {
                    "observation": "Click completed without a verified receipt",
                    "observed_at": now(),
                },
            )
        except Exception as exc:
            record.update(
                status="unconfirmed",
                blockers=[
                    "Submit click/receipt check failed: "
                    + type(exc).__name__
                    + "; no automatic retry"
                ],
            )
            return store.outcome(
                key,
                record,
                {
                    "observation": "Exception after submission intent",
                    "type": type(exc).__name__,
                },
            )
    except Exception as exc:
        # Navigation/loading failures remain reviewable. An existing intent stays protected.
        record.update(
            status=status if protected else "needs_review",
            blockers=["Browser/form error: " + type(exc).__name__ + "; tab kept open"],
        )
        store.put(key, record, "controller_error")
        return record
    finally:
        page.remove_listener("request", requested)
        page.remove_listener("requestfinished", finished)
        page.remove_listener("requestfailed", finished)
