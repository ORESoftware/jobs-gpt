"""Portable schemas, identity checks and input validation. No applicant defaults."""

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .forms import HOSTS


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def read_profile(path):
    path = Path(path).resolve()
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("fields"), dict):
        raise ValueError("Profile must contain a fields object")
    fields = data["fields"]
    if not fields.get("email") or not (
        fields.get("full_name") or fields.get("first_name")
    ):
        raise ValueError("Profile needs email and a full or first name")
    resume = Path(data.get("resume_file", ""))
    if not resume.is_absolute():
        resume = path.parent / resume
    if not resume.is_file() or not resume.read_bytes().startswith(b"%PDF"):
        raise ValueError(
            "resume_file must point to an existing PDF, relative to the profile or absolute"
        )
    data = dict(data)
    data["_resume_path"] = str(resume.resolve())
    data["_resume_sha256"] = hashlib.sha256(resume.read_bytes()).hexdigest()
    data["_profile_digest"] = digest(
        {k: v for k, v in data.items() if not k.startswith("_")}
    )
    return data


def validate_real_applicant(profile):
    email = str(profile["fields"]["email"]).lower()
    domain = email.rsplit("@", 1)[-1]
    if (
        "@" not in email
        or domain in ("example.com", "example.org", "example.net", "localhost")
        or domain.endswith((".test", ".invalid", ".example"))
    ):
        raise ValueError("Example/test profiles cannot be used with --submit")


def identity(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError(
            "Use a direct HTTPS Ashby, Greenhouse or Lever application URL"
        )
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)
    if "greenhouse" in parsed.hostname:
        if parts and parts[0] == "embed":
            board = (query.get("for") or [""])[0]
            posting = (query.get("token") or [""])[0]
        else:
            board = parts[0] if parts else ""
            posting = (
                parts[parts.index("jobs") + 1]
                if "jobs" in parts and len(parts) > parts.index("jobs") + 1
                else ""
            )
        ats = "greenhouse"
    else:
        ats = "ashby" if "ashbyhq" in parsed.hostname else "lever"
        board = parts[0] if parts else ""
        posting = parts[1] if len(parts) > 1 else ""
    if not board or not re.fullmatch(
        r"[0-9]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", posting
    ):
        raise ValueError("URL must identify a specific employer job posting")
    return ats, board.casefold(), posting.casefold()


def job_key(job):
    return ":".join(identity(job["url"]))


def assert_job_url(job, url):
    if identity(job["url"]) != identity(url):
        raise ValueError("Open form does not match the intended employer job")


def read_jobs(path):
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError("Jobs file must be an array")
    unique = {}
    for job in data:
        if not all(job.get(k) for k in ("company", "role", "url")):
            raise ValueError("Every job needs company, role and a direct ATS URL")
        key = job_key(job)
        if key in unique:
            continue
        unique[key] = job
    return list(unique.values())


def eligibility_issues(job, profile, max_age_hours=24):
    review = job.get("eligibility", {})
    if not isinstance(review, dict) or review.get("verified") is not True:
        return ["Eligibility/location review required"]
    if (
        not review.get("reviewed_by")
        or not review.get("evidence")
        or not review.get("source_url")
    ):
        return ["Eligibility review needs reviewer, evidence and source URL"]
    if review.get("profile_digest") != profile["_profile_digest"]:
        return ["Eligibility review does not match the current applicant profile"]
    try:
        checked = dt.datetime.fromisoformat(review["checked_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None:
            raise ValueError("timezone required")
        age = (dt.datetime.now(dt.timezone.utc) - checked).total_seconds()
        if not 0 <= age <= max_age_hours * 3600:
            raise ValueError("expired/future review")
    except (KeyError, ValueError, TypeError):
        return [
            "Eligibility review is expired, future-dated or missing a valid checked_at"
        ]
    return []
