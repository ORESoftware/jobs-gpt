"""Rolling-window candidate discovery from employer feeds, never eligibility approval."""

import datetime as dt
import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import job_key, now


def fetch(url):
    req = Request(url, headers={"User-Agent": "jobs-gpt/0.1 (public job discovery)"})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def collect(
    boards,
    days=10,
    title_pattern=r"engineer|developer|compiler|infrastructure",
    remote=True,
    loader=fetch,
    clock=None,
):
    if not 0 < days <= 365:
        raise ValueError("days must be between 1 and 365")
    current = clock or dt.datetime.now(dt.timezone.utc)
    cutoff = current - dt.timedelta(days=days)
    result = {}
    errors = []
    for board in boards:
        name = board["board"]
        ats = board["ats"]
        company = board.get("company", name)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid board name")
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
        for raw in data.get("jobs", []):
            title = raw.get("title", "")
            if not re.search(title_pattern, title, re.I):
                continue
            if ats == "ashby":
                if raw.get("isListed") is False:
                    continue
                published = raw.get("publishedAt")
                link = raw.get("applyUrl") or raw.get("jobUrl")
                location = raw.get("location", "")
                isremote = (
                    raw.get("isRemote") is True or raw.get("workplaceType") == "Remote"
                )
                semantics = "last_published_may_be_republication"
            else:
                # Never substitute updated_at when the feed omits a publication date.
                published = raw.get("first_published")
                link = raw.get("absolute_url")
                location = (raw.get("location") or {}).get("name", "")
                isremote = bool(re.search(r"\bremote\b", location + " " + title, re.I))
                semantics = "first_published"
            if remote and not isremote:
                continue
            if not published:
                errors.append(
                    {
                        "board": name,
                        "job_id": raw.get("id"),
                        "error": "Missing publication date; excluded",
                    }
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
            except (ValueError, TypeError):
                errors.append(
                    {
                        "board": name,
                        "job_id": raw.get("id"),
                        "error": "Unsupported direct URL or invalid publication date",
                    }
                )
    return {
        "jobs": sorted(result.values(), key=lambda j: j["posted_at"], reverse=True),
        "errors": errors,
        "cutoff": cutoff.isoformat(),
        "checked_at": current.isoformat(),
    }
