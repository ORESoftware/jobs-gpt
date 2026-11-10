"""Explicit source review, bound to applicant, resume, job and question."""

import json
from pathlib import Path

from pypdf import PdfReader

from .forms import bank_answer, discover, norm, profile_value
from .models import digest, job_key, now, read_json


def source_text(profile, kind):
    if kind == "profile":
        return json.dumps(
            {k: v for k, v in profile.items() if not k.startswith("_")},
            ensure_ascii=False,
        )
    if kind == "resume":
        return "\n".join(
            p.extract_text() or "" for p in PdfReader(profile["_resume_path"]).pages
        )
    raise ValueError(
        "Source must be profile or resume; add explicit user answers to the private profile first"
    )


def approved(bank, profile, job):
    result = []
    for entry in bank:
        if (
            entry.get("verified") is not True
            or not entry.get("reviewed_by")
            or entry.get("job_key") != job_key(job)
            or entry.get("profile_digest") != profile["_profile_digest"]
            or entry.get("resume_sha256") != profile["_resume_sha256"]
            or entry.get("approval_digest")
            != digest({k: v for k, v in entry.items() if k != "approval_digest"})
        ):
            continue
        quote = entry.get("source_quote", "")
        try:
            valid = quote and norm(quote) in norm(
                source_text(profile, entry.get("source_kind"))
            )
        except (ValueError, KeyError):
            valid = False
        if valid:
            result.append(entry)
    return result


def approve(path, profile, job, question, answer, source_kind, quote, reviewer):
    if (
        not isinstance(answer, (str, bool, int, float, list))
        or isinstance(answer, list)
        and not all(isinstance(v, (str, bool, int, float)) for v in answer)
    ):
        raise ValueError("Answer must be a scalar or a list of choices")
    if not reviewer.strip() or not question.strip() or not quote.strip():
        raise ValueError("Reviewer, question and exact source quote are required")
    if norm(quote) not in norm(source_text(profile, source_kind)):
        raise ValueError("Quote does not occur in the selected source")
    # This command records an actual human/agent source review; quote matching alone is
    # not a semantic proof. The reviewing agent must check that it supports the answer.
    entry = {
        "job_key": job_key(job),
        "question": question,
        "answer": answer,
        "verified": True,
        "reviewed_by": reviewer,
        "source_kind": source_kind,
        "source_quote": quote,
        "profile_digest": profile["_profile_digest"],
        "resume_sha256": profile["_resume_sha256"],
        "reviewed_at": now(),
    }
    entry["approval_digest"] = digest(entry)
    bank = read_json(path) if Path(path).exists() else []
    bank = [
        a
        for a in bank
        if not (
            a.get("job_key") == entry["job_key"]
            and norm(a.get("question", "")) == norm(question)
        )
    ]
    bank.append(entry)
    Path(path).write_text(json.dumps(bank, indent=2, ensure_ascii=False) + "\n")
    return entry


def revalidate(scope, profile, answers, job):
    """Recheck restored/manual values rather than trusting stale per-tab action IDs."""
    actions = []
    for f in discover(scope):
        expected = profile_value(f, profile["fields"])
        source = "profile"
        if expected is None:
            expected = bank_answer(f, answers, job)
            source = "verified_answer"
        if f["kind"] == "file":
            continue  # Upload is verified against the actual File below.
        value = f["value"]
        if (
            f["kind"] == "radio"
            and value
            and expected is not None
            and norm(expected) == norm(f["label"])
        ):
            expected = value
        if expected is True and f["kind"] == "checkbox":
            expected = value if value else None
        if expected is False and not value:
            continue
        if expected is not None and norm(expected) == norm(value):
            actions.append(
                {
                    "field": f["label"],
                    "field_id": f["id"],
                    "source": source,
                    "value": value,
                }
            )
    return actions
