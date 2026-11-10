"""DOM adapters shared by Ashby, Greenhouse (including embeds), and Lever.
Site text is data, never executable instructions. No arbitrary AI selectors/clicks.
"""

import re
import unicodedata

HOSTS = {
    "jobs.ashbyhq.com",
    "job-boards.greenhouse.io",
    "boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "job-boards.eu.greenhouse.io",
    "jobs.lever.co",
    "jobs.eu.lever.co",
}
CONFIRM = re.compile(
    r"thank(?:s| you) for (?:your )?applying|thank you for your application|"
    r"(?:your )?application (?:has been |was )?(?:successfully )?(?:submitted|received)|"
    r"we(?:'ve| have) received your application",
    re.I,
)
SENSITIVE = re.compile(
    r"authoriz|sponsor|visa|citizen|salary|compensation|gender|race|ethnic|"
    r"disab|veteran|criminal|convict|consent|agree|acknowledge|privacy|terms|"
    r"relocat|travel|notice|start date|clearance|non.?compete|how did you hear|where did you hear|passport",
    re.I,
)


def norm(s):
    return (
        re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s)).replace("*", ""))
        .strip()
        .casefold()
    )


# Attribute IDs survive rescans; react inputs and their role=combobox ancestor are deduplicated.
DISCOVER = r"""() => {
 const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[aria-hidden=true]');
 const groupSelector = 'fieldset,[role=group],.ashby-application-form-question,.ashby-application-form-field-entry,.field,.field-wrapper,.select__container,.application-question,[data-field]';
 const txt = e => (e?.innerText || e?.textContent || '').trim();
 const labelTxt = e => { if (!e) return ''; const c=e.cloneNode(true); c.querySelectorAll('input,textarea,select,button,[role=combobox]').forEach(x=>x.remove()); return txt(c); };
 const label = e => {
   let s = [...(e.labels || [])].map(labelTxt).join(' ');
   if (!s) s = (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => labelTxt(document.getElementById(id))).join(' ').trim();
   if (!s) s = e.getAttribute('aria-label') || '';
   if (!s) {
     const box = e.closest(groupSelector);
     s = labelTxt(box?.querySelector('legend, label, [class*=label], [class*=Label]'));
   }
   return (s || e.placeholder || e.name || e.id || 'Unlabelled field').slice(0,700);
 };
 const all = [...document.querySelectorAll('input,textarea,select,[role=combobox],[role=checkbox],[contenteditable=true],button[aria-pressed]')];
 return all.filter(e => {
   if ((['hidden','submit','button','reset'].includes(e.type) && !e.matches('button[aria-pressed]')) || e.disabled || e.getAttribute('aria-disabled') === 'true') return false;
   if (!visible(e) && e.type !== 'file') return false;
   if (e.matches('button[aria-pressed]') && e.parentElement.querySelector('button[aria-pressed]') !== e) return false;
   if (e.matches('[role=combobox]') && !e.matches('input,select') && e.querySelector('input')) return false;
   return true;
 }).map(e => {
   if (!e.dataset.jpId) e.dataset.jpId = 'jp-' + (++window.__jpSeq || (window.__jpSeq=1));
   const group = e.closest(groupSelector);
   let l = label(e);
   if (e.type === 'file' && /^(resume|cv)$/i.test(e.id || e.name || '')) l = 'Resume';
   if (e.type === 'file' && /cover.?letter/i.test(e.id || e.name || '')) l = 'Cover letter';
   const kind = e.matches('button[aria-pressed]') ? 'toggle' : ['combobox','checkbox'].includes(e.getAttribute('role')) ? e.getAttribute('role') : (e.type || (e.isContentEditable ? 'contenteditable' : e.tagName.toLowerCase()));
   const selected = txt(group?.querySelector('[class*=singleValue], [class*=SingleValue], .select__single-value'));
   let value = kind === 'toggle' ? txt(e.parentElement.querySelector('button[aria-pressed=true]')) : e.type === 'file' ? [...(e.files||[])].map(f=>f.name).join(', ') :
       ['checkbox','radio'].includes(e.type) ? (e.checked ? (e.value || 'true') : '') :
       e.getAttribute('role') === 'checkbox' ? (e.getAttribute('aria-checked')==='true' ? 'true' : '') :
       e.tagName === 'SELECT' ? (e.multiple ? [...e.selectedOptions].filter(o=>o.value).map(o=>o.text.trim()) : (e.value ? e.selectedOptions[0]?.text.trim() || '' : '')) :
       (kind === 'combobox' ? selected : '') || (e.isContentEditable ? txt(e) : e.value || '');
   let required = !!e.required || e.getAttribute('aria-required')==='true' ||
       !!group?.querySelector('[aria-required=true], .required, [class*=required]') || /\*/.test(l);
   const groupLabel = labelTxt(group?.querySelector('legend,.ashby-application-form-question-title,:scope > label'));
   const checkboxGroup = e.type === 'checkbox' && group?.matches('fieldset,[role=group]') && group.querySelectorAll('input[type=checkbox]').length > 1;
   let groupSatisfied = false;
   if (e.type === 'radio') {
     const peers = all.filter(x=>x.type==='radio' && x.name===e.name && x.form===e.form);
     if (peers.some(x=>x.required)) required=true;
     groupSatisfied = peers.some(x=>x.checked);
   } else if (checkboxGroup && !e.required) {
     groupSatisfied = !!group.querySelector('input[type=checkbox]:checked');
   }
   return {id:e.dataset.jpId, label:l, question:groupLabel || l, kind, required, value, group_satisfied:groupSatisfied, name:e.name||'',
       invalid:e.getAttribute('aria-invalid')==='true' || (!!e.willValidate && !e.validity.valid),
       options:kind === 'toggle' ? [...e.parentElement.querySelectorAll('button[aria-pressed]')].map(txt) : e.tagName==='SELECT' ? [...e.options].filter(o=>!o.disabled && o.value).map(o=>o.text.trim()) : [],
       autocomplete:e.autocomplete||'', multiple:!!e.multiple};
 });
}"""


def scopes(page):
    # Never inspect arbitrary third-party frames such as CAPTCHA/payment widgets.
    from urllib.parse import urlparse

    result = [page.main_frame]
    for frame in page.frames:
        if frame != page.main_frame and urlparse(frame.url).hostname in HOSTS:
            result.append(frame)
    return result


def discover(scope):
    return scope.evaluate(DISCOVER)


def element(scope, field):
    return scope.locator('[data-jp-id="' + field["id"] + '"]')


def profile_value(field, profile):
    if field["kind"] not in ("text", "email", "tel", "url", "textarea"):
        return None
    label = norm(field["label"])
    patterns = {
        "first_name": r"^(first name|given name)$",
        "legal_first_name": r"^legal first name$",
        "legal_last_name": r"^legal last name$",
        "last_name": r"^(last name|family name|surname)$",
        "full_name": r"^(full name|full legal name|name|legal name|preferred first & last name|first and last name)$",
        "preferred_name": r"^preferred (?:first )?name$",
        "email": r"^(email|email address)$",
        "phone": r"^(phone|phone number|mobile|mobile number)$",
        "linkedin": r"^(?:link to your )?linkedin(?: profile| url| profile url)?$",
        "github": r"^(?:link to your )?github(?: profile| url| profile url)?$",
        "website": r"^(website|portfolio|personal website|portfolio url)$",
        "city": r"^(city|current city)$",
    }
    for key, pattern in patterns.items():
        if re.fullmatch(pattern, label):
            value = profile.get(key)
            if key == "full_name":
                value = profile.get("full_name") or " ".join(
                    filter(None, [profile.get("first_name"), profile.get("last_name")])
                )
            return value
    # Old Greenhouse inputs may have only ID/name but no accessible label.
    if field["label"] == "Unlabelled field":
        return None
    return None


def bank_answer(field, answers, job):
    """Only exact question matches; optionally restrict an answer to one job URL."""
    for a in answers:
        if a.get("verified") is not True:
            continue
        if a.get("job_url") and a["job_url"].rstrip("/") != job["url"].rstrip("/"):
            continue
        if norm(a.get("question", "")) == norm(
            field.get("question", field["label"])
        ) and field["kind"] in ("checkbox", "radio"):
            choices = a.get("answer")
            choices = choices if isinstance(choices, list) else [choices]
            if any(norm(c) == norm(field["label"]) for c in choices):
                return True if field["kind"] == "checkbox" else field["label"]
        if norm(a.get("question", "")) == norm(field["label"]):
            return a.get("answer")
    return None


def set_value(scope, field, value):
    el = element(scope, field)
    kind = field["kind"]
    if kind == "toggle":
        choice = "Yes" if value is True else "No" if value is False else str(value)
        option = el.locator("..").get_by_role(
            "button", name=re.compile("^" + re.escape(choice) + "$", re.I)
        )
        if option.count() != 1:
            raise ValueError("Exactly one matching toggle option was not found")
        option.click(timeout=3000)
        if option.get_attribute("aria-pressed") != "true":
            raise ValueError("Toggle selection was not verified")
    elif kind == "select-one":
        # Exact visible text prevents choosing a similarly named location/qualification.
        el.select_option(label=str(value), timeout=3000)
    elif kind == "select-multiple":
        el.select_option(
            label=value if isinstance(value, list) else [str(value)], timeout=3000
        )
    elif kind in ("checkbox", "radio"):
        if kind == "radio":
            # Field label identifies this option. Do not infer a yes/no choice.
            if norm(value) != norm(el.get_attribute("value") or "") and norm(
                value
            ) != norm(field["label"]):
                raise ValueError("Radio answer does not match this option")
            el.check(timeout=3000)
        else:
            if value not in (True, False, "Yes", "No", "true", "false"):
                raise ValueError("Checkbox requires an explicit boolean answer")
            checked = value in (True, "Yes", "true")
            if (
                el.get_attribute("role") == "checkbox"
                and el.get_attribute("type") != "checkbox"
            ):
                if (el.get_attribute("aria-checked") == "true") != checked:
                    el.click(timeout=3000)
            else:
                el.set_checked(checked, timeout=3000)
    elif kind == "combobox" or el.get_attribute("aria-autocomplete"):
        el.click(timeout=3000)
        if el.evaluate("e => e.tagName === 'INPUT'"):
            el.fill(str(value), timeout=3000)
        if el.get_attribute("aria-expanded") == "false":
            el.press("ArrowDown", timeout=1500)
        options = scope.get_by_role(
            "option", name=re.compile("^" + re.escape(str(value)) + "$", re.I)
        )
        options.first.wait_for(state="visible", timeout=4000)
        if options.count() != 1:
            raise ValueError("Dropdown option is ambiguous")
        options.click(timeout=3000)
        # Merely typing in a search input is never proof of a selected option.
        el.press("Tab")
        selected = el.evaluate(
            "e => { const b=e.closest('.field,.field-wrapper,.select__container,.ashby-application-form-question,.ashby-application-form-field-entry,[data-field],.application-question'); return b?.querySelector('[class*=singleValue],[class*=SingleValue],.select__single-value')?.textContent || ''; }"
        )
        if not selected and not el.input_value():
            raise ValueError("Dropdown selection not verified")
    elif kind not in ("file",):
        el.fill(str(value), timeout=3000)


def fill(scope, profile, resume, answers, job, actions):
    for field in discover(scope):
        if field["value"]:
            continue
        try:
            if field["kind"] == "file":
                # Never attach the CV to arbitrary required files or a cover letter input.
                if re.search(
                    r"\b(resum[eé]|cv|curriculum vitae)\b", field["label"], re.I
                ) and not re.search(r"cover letter", field["label"], re.I):
                    element(scope, field).set_input_files(str(resume), timeout=5000)
                    actions.append(
                        {
                            "field": field["label"],
                            "field_id": field["id"],
                            "source": "resume",
                            "value": resume.name,
                        }
                    )
                continue
            value = profile_value(field, profile)
            source = "profile"
            if value is None:
                value = bank_answer(field, answers, job)
                source = "verified_answer"
            if value is not None:
                set_value(scope, field, value)
                snapshot = next(
                    (f["value"] for f in discover(scope) if f["id"] == field["id"]),
                    None,
                )
                actions.append(
                    {
                        "field": field["label"],
                        "field_id": field["id"],
                        "source": source,
                        "value": snapshot,
                    }
                )
        except Exception as exc:
            actions.append(
                {
                    "field": field["label"],
                    "error": type(exc).__name__ + ": " + str(exc)[:180],
                }
            )


def blockers(scope, actions, reviewed=False, ignore_form_errors=False):
    issues = []
    human_challenges = scope.locator(
        'iframe[src*="recaptcha"], iframe[src*="hcaptcha"]'
    )
    for index in range(human_challenges.count()):
        iframe = human_challenges.nth(index)
        src = iframe.get_attribute("src") or ""
        if "size=invisible" in src and "/anchor" in src:
            continue
        if iframe.is_visible():
            return ["CAPTCHA requires manual completion"]
    fields = discover(scope)
    if not fields:
        return [
            "No application controls found (loading, login, closed job, or unsupported markup)"
        ]
    # Duplicate option labels/radio peers are intentionally collapsed for reporting only.
    approved = [
        a
        for a in actions
        if a.get("source") in ("profile", "resume", "verified_answer")
    ]
    trusted = {a["field_id"]: a.get("value") for a in approved if a.get("field_id")}
    label_counts = {
        norm(f["label"]): sum(
            norm(other["label"]) == norm(f["label"]) for other in fields
        )
        for f in fields
    }

    def same_value(f, value):
        if value is None:
            return False
        if f["kind"] == "tel":
            return re.sub(r"\D", "", str(value)) == re.sub(r"\D", "", str(f["value"]))
        return norm(value) == norm(f["value"])

    for f in fields:
        if f["required"] and not f["value"] and not f.get("group_satisfied"):
            issues.append("Required: " + f.get("question", f["label"]))
        elif f["value"] and f["invalid"]:
            issues.append("Invalid: " + f["label"])
        verified = same_value(f, trusted.get(f["id"])) or (
            label_counts[norm(f["label"])] == 1
            and any(
                norm(a["field"]) == norm(f["label"]) and same_value(f, a.get("value"))
                for a in approved
            )
        )
        if f["value"] and not verified and not reviewed:
            issues.append("Review existing/AI answer: " + f["label"])
    errors = scope.locator(
        "[role=alert], .field-error, .error-message, [class*=errorMessage]"
    )
    for i in range(0 if ignore_form_errors else min(errors.count(), 20)):
        if errors.nth(i).is_visible():
            text = errors.nth(i).inner_text().strip()
            if text:
                issues.append("Form error: " + text[:220])
    captcha = scope.locator('iframe[src*="recaptcha"], iframe[src*="hcaptcha"]')
    for i in range(captcha.count()):
        frame = captcha.nth(i)
        src = frame.get_attribute("src") or ""
        # An invisible score widget is not an unsolved human challenge. Try the normal
        # form workflow; stop if an interactive challenge actually appears.
        if "size=invisible" in src and "/anchor" in src:
            continue
        if frame.is_visible():
            issues.append("CAPTCHA requires manual completion")
            break
    return sorted(set(issues))


def submit_buttons(scope):
    return scope.get_by_role(
        "button",
        name=re.compile(
            r"^(submit application|submit|send application|apply for this job)$", re.I
        ),
    )


def confirmation(scope):
    body = scope.locator("body").inner_text(timeout=5000)
    return [m.group(0) for m in CONFIRM.finditer(body)]


def confirmation_after(scope, before, timeout=12000):
    # Require a new confirmation AND disappearance of the final submission control.
    import time

    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        try:
            messages = confirmation(scope)
            buttons = submit_buttons(scope)
            active = any(buttons.nth(i).is_visible() for i in range(buttons.count()))
            fresh = [
                m
                for m in messages
                if m.casefold() not in {s.casefold() for s in before}
            ]
            if fresh and not active:
                return fresh[0]
        except Exception:
            pass
        scope.page.wait_for_timeout(200)
    return None


def open_application(page):
    for scope in scopes(page):
        fields = discover(scope)
        if any(
            f["kind"] in ("email", "file")
            or norm(f["label"]) in ("email", "email address")
            for f in fields
        ):
            return scope
    # Description tabs often have a single explicit Apply button or link.
    for role in ("button", "link"):
        matches = page.get_by_role(
            role,
            name=re.compile(
                r"^(apply|apply now|apply for this job|application)$", re.I
            ),
        )
        for i in range(matches.count()):
            el = matches.nth(i)
            if not el.is_visible():
                continue
            if role == "link":
                from urllib.parse import urljoin, urlparse

                href = urljoin(page.url, el.get_attribute("href") or "")
                if urlparse(href).hostname not in HOSTS:
                    continue
                page.goto(href, wait_until="domcontentloaded")
            else:
                el.click(timeout=4000)
            page.wait_for_timeout(800)
            break
        else:
            continue
        break
    for _ in range(20):
        for scope in scopes(page):
            if discover(scope):
                return scope
        page.wait_for_timeout(250)
    return page.main_frame
