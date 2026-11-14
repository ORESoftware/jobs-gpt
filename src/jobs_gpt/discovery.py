"""Rolling-window discovery from bounded public ATS feeds; never eligibility approval."""

import datetime as dt
import json
import re
from urllib.parse import quote
from urllib.request import Request

from .models import job_key, now
from .network import open_no_redirect

MAX_FEED_BYTES = 8 * 1024 * 1024


def fetch(url):
    """Keep employer discovery on its HTTPS origin and bound untrusted responses."""
    req = Request(url, headers={"User-Agent": "jobs-gpt/0.2 (public job discovery)"})
    with open_no_redirect(req, timeout=30) as response:
        body = response.read(MAX_FEED_BYTES + 1)
    if len(body) > MAX_FEED_BYTES:
        raise ValueError("Employer feed exceeds 8 MiB")
    return json.loads(body)


def collect(
    boards,
    days=10,
    title_pattern=r"engineer|developer|compiler|infrastructure",
    remote=True,
    loader=fetch,
    clock=None,
    exclude_title_pattern=None,
):
    if not 0 < days <= 365:
        raise ValueError("days must be between 1 and 365")
    if not isinstance(boards, list):
        raise ValueError("boards must be an array")
    try:
        include_title = re.compile(title_pattern, re.I)
        exclude_title = (
            re.compile(exclude_title_pattern, re.I)
            if exclude_title_pattern
            else None
        )
    except (re.error, TypeError):
        raise ValueError("Invalid discovery title filter regular expression") from None
    current = clock or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Discovery clock must have a time zone")
    cutoff = current - dt.timedelta(days=days)
    result = {}
    errors = []
    for board in boards:
        if not isinstance(board, dict):
            raise ValueError("Every board configuration must be an object")
        name = board.get("board")
        ats = board.get("ats")
        company = board.get("company", name)
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid board name")
        if not isinstance(company, str) or not company.strip():
            raise ValueError("Invalid company name")
        if ats == "ashby":
            url = "https://api.ashbyhq.com/posting-api/job-board/" + quote(name)
        elif ats == "greenhouse":
            url = (
                "https://boards-api.greenhouse.io/v1/boards/"
                + quote(name)
                + "/jobs?content=true"
            )
        else:
            raise ValueError(
                "Discovery supports ashby and greenhouse; direct Lever URLs work in run"
            )
        try:
            data = loader(url)
        except Exception as exc:
            errors.append({"board": name, "ats": ats, "error": type(exc).__name__})
            continue
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            errors.append(
                {"board": name, "ats": ats, "error": "Malformed job feed; excluded"}
            )
            continue
        for raw in data["jobs"]:
            if not isinstance(raw, dict):
                errors.append(
                    {"board": name, "error": "Malformed job entry; excluded"}
                )
                continue
            job_id = raw.get("id")
            title = raw.get("title")
            if not isinstance(title, str):
                errors.append(
                    {"board": name, "job_id": job_id, "error": "Malformed job title; excluded"}
                )
                continue
            if not include_title.search(title) or (
                exclude_title and exclude_title.search(title)
            ):
                continue
            if ats == "ashby":
                if raw.get("isListed") is False:
                    continue
                published = raw.get("publishedAt")
                link = raw.get("applyUrl") or raw.get("jobUrl")
                location = raw.get("location") or ""
                if isinstance(location, dict):
                    location = location.get("name") or ""
                isremote = (
                    raw.get("isRemote") is True or raw.get("workplaceType") == "Remote"
                )
                semantics = "last_published_may_be_republication"
            else:
                # Never use updated_at instead of first_published.
                published = raw.get("first_published")
                link = raw.get("absolute_url")
                location_data = raw.get("location") or {}
                location = (
                    location_data.get("name") or ""
                    if isinstance(location_data, dict)
                    else location_data
                )
                semantics = "first_published"
            if not isinstance(location, str):
                errors.append(
                    {"board": name, "job_id": job_id, "error": "Malformed location; excluded"}
                )
                continue
            if ats == "greenhouse":
                isremote = bool(re.search(r"\bremote\b", location + " " + title, re.I))
            if remote and not isremote:
                continue
            if not published:
                errors.append(
                    {"board": name, "job_id": job_id, "error": "Missing publication date; excluded"}
                )
                continue
            if not isinstance(published, str) or not isinstance(link, str):
                errors.append(
                    {"board": name, "job_id": job_id, "error": "Invalid publication date or URL"}
                )
                continue
            try:
                stamp = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
                if stamp.tzinfo is None or not cutoff <= stamp <= current:
                    continue
                job = {
                    "company": company,
                    "role": title,
                    "url": link,
                    "location": location,
                    "posted_at": stamp.isoformat(),
                    "date_semantics": semantics,
                    "discovered_at": now(),
                    "discovery_source": url,
                    "eligibility": {"verified": False},
                }
                result[job_key(job)] = job
            except (ValueError, TypeError, AttributeError):
                errors.append(
                    {
                        "board": name,
                        "job_id": job_id,
                        "error": "Unsupported direct URL or invalid publication date",
                    }
                )
    return {
        "jobs": sorted(result.values(), key=lambda j: j["posted_at"], reverse=True),
        "errors": errors,
        "cutoff": cutoff.isoformat(),
        "checked_at": current.isoformat(),
    }
