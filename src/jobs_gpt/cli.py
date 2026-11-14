"""CLI keeps private inputs and browser state outside the source checkout."""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from .models import job_key, read_jobs, read_json, read_profile, validate_real_applicant
from .privacy import private_path, write_private
from .state import Store, workspace_lock


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evidence-backed job application automation"
    )
    parser.add_argument(
        "--workspace", type=Path, default=Path.home() / ".local/share/jobs-gpt"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    browser = sub.add_parser("browser", help="Start detached background Chromium")
    browser.add_argument("--port", type=int, default=0)
    discover = sub.add_parser("discover")
    discover.add_argument("--boards", required=True)
    discover.add_argument("--days", type=int, default=10)
    discover.add_argument(
        "--title", default=r"engineer|developer|compiler|infrastructure"
    )
    discover.add_argument("--include-onsite", action="store_true")
    discover.add_argument("--output", required=True)
    run = sub.add_parser("run")
    run.add_argument("--jobs", required=True)
    run.add_argument("--profile", required=True)
    run.add_argument("--answers")
    run.add_argument("--submit", action="store_true")
    run.add_argument("--ai", action="store_true")
    run.add_argument("--model", default=os.environ.get("GEMINI_MODEL"))
    run.add_argument("--endpoint")
    run.add_argument("--only", help="Exact job key")
    fingerprint = sub.add_parser("fingerprint")
    fingerprint.add_argument("--profile", required=True)
    approve = sub.add_parser(
        "approve",
        help="Record an actual independent source review, never a model self-approval",
    )
    for name in (
        "jobs",
        "profile",
        "answers",
        "job-key",
        "question",
        "answer-json",
        "source-kind",
        "quote",
        "reviewer",
    ):
        approve.add_argument("--" + name, required=True)
    report = sub.add_parser("report")
    report.add_argument("--output")
    migrate = sub.add_parser(
        "import-legacy",
        help="Preserve prior receipts/uncertainty from a private pilot ledger",
    )
    migrate.add_argument("--tracker", required=True)
    check = sub.add_parser(
        "doctor", help="Read-only environment, privacy and ledger preflight"
    )
    check.add_argument("--profile")
    check.add_argument(
        "--browser",
        action="store_true",
        help="Also check local browser identity without opening tabs",
    )
    eligibility = sub.add_parser(
        "review-eligibility", help="Record an actual job-specific eligibility review"
    )
    for name in ("jobs", "profile", "job-key", "evidence", "source-url", "reviewer"):
        eligibility.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            from .checks import doctor

            result = doctor(args.workspace, args.profile, args.browser)
            print(json.dumps(result, indent=2))
            if not result["ok"]:
                parser.exit(1)
            return
        if args.command == "review-eligibility":
            from .models import assert_job_url, now

            profile = read_profile(args.profile)
            jobs = read_jobs(args.jobs)
            job = next((j for j in jobs if job_key(j) == args.job_key), None)
            if not job or not args.evidence.strip() or not args.reviewer.strip():
                raise ValueError("Matching job, evidence and reviewer are required")
            # An HTTPS URL to some other role or domain is not evidence for
            # the specific ATS posting that this command will authorize.
            try:
                assert_job_url(job, args.source_url)
            except ValueError:
                raise ValueError(
                    "Eligibility evidence needs the same direct HTTPS ATS posting URL"
                ) from None
            job["eligibility"] = {
                "verified": True,
                "job_key": job_key(job),
                "reviewed_by": args.reviewer,
                "evidence": args.evidence,
                "source_url": args.source_url,
                "checked_at": now(),
                "profile_digest": profile["_profile_digest"],
                "resume_sha256": profile["_resume_sha256"],
            }
            write_private(args.jobs, jobs)
            print("Job-specific eligibility review recorded.")
            return
        if args.command == "discover":
            from .discovery import collect

            result = collect(
                read_json(args.boards), args.days, args.title, not args.include_onsite
            )
            Path(args.output).write_text(json.dumps(result["jobs"], indent=2) + "\n")
            Path(str(args.output) + ".audit.json").write_text(
                json.dumps({k: v for k, v in result.items() if k != "jobs"}, indent=2)
                + "\n"
            )
            print(
                json.dumps(
                    {
                        "candidates": len(result["jobs"]),
                        "discovery_issues": len(result["errors"]),
                    }
                )
            )
            return
        if args.command == "fingerprint":
            profile = read_profile(args.profile)
            print(
                json.dumps(
                    {k: profile[k] for k in ("_profile_digest", "_resume_sha256")},
                    indent=2,
                )
            )
            return
        if args.command == "approve":
            from .reviews import approve as source_approve

            job = next(
                (j for j in read_jobs(args.jobs) if job_key(j) == args.job_key), None
            )
            if not job:
                raise ValueError("No matching job key")
            source_approve(
                args.answers,
                read_profile(args.profile),
                job,
                args.question,
                json.loads(args.answer_json),
                args.source_kind,
                args.quote,
                args.reviewer,
            )
            print("Source review recorded; rerun to fill the approved answer.")
            return
        store = Store(args.workspace)
        if args.command == "report":
            records = store.all()
            report = {
                "counts": dict(Counter(r["status"] for r in records.values())),
                "applications": records,
            }
            output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            if args.output:
                write_private(args.output, report)
            else:
                print(output)
            return
        if args.command == "import-legacy":
            records = read_json(args.tracker)
            records = records.values() if isinstance(records, dict) else records
            count = 0
            with workspace_lock(args.workspace):
                for item in records:
                    job = item.get("job") or item
                    # Some legacy trackers put job metadata directly in each record.
                    try:
                        key = job_key(job)
                    except ValueError:
                        import re
                        from urllib.parse import parse_qs, urlparse

                        source = item.get("source_api", "")
                        board = re.search(r"/boards/([A-Za-z0-9_-]+)/jobs", source)
                        posting = (
                            parse_qs(urlparse(job.get("url", "")).query).get("gh_jid")
                            or [""]
                        )[0]
                        if not board or not posting.isdigit():
                            raise ValueError(
                                "Legacy custom URL needs a recorded Greenhouse board API and gh_jid"
                            ) from None
                        job = dict(
                            job,
                            original_url=job["url"],
                            url="https://job-boards.greenhouse.io/"
                            + board.group(1)
                            + "/jobs/"
                            + posting,
                        )
                        key = job_key(job)
                    status = item.get("status", "needs_review")
                    if (
                        item.get("submit_attempt_at")
                        or item.get("submit_attempt_utc")
                        or item.get("submit_clicked")
                    ) and status not in (
                        "submitted",
                        "application_limit",
                        "captcha_blocked",
                    ):
                        status = "unconfirmed"
                    if status not in (
                        "submitted",
                        "unconfirmed",
                        "application_limit",
                        "captcha_blocked",
                        "submitting",
                    ):
                        status = "needs_review"
                    store.import_record(key, dict(item, job=job, status=status))
                    count += 1
            print(json.dumps({"records_imported_or_preserved": count}))
            return
        from playwright.sync_api import sync_playwright

        from .browser import local_endpoint, start, version

        if args.command == "browser":
            with workspace_lock(args.workspace), sync_playwright() as pw:
                print(json.dumps(start(pw, args.workspace, args.port), indent=2))
            return
        profile = read_profile(args.profile)
        jobs = read_jobs(args.jobs)
        if args.submit:
            validate_real_applicant(profile)
        if args.ai and not args.model:
            raise ValueError(
                "Choose --model or set GEMINI_MODEL; model IDs are not hardcoded"
            )
        if args.only:
            jobs = [j for j in jobs if job_key(j) == args.only]
        if not jobs:
            raise ValueError("No jobs match this run")
        bank = read_json(private_path(args.answers)) if args.answers else []
        from .runner import process

        with workspace_lock(args.workspace), sync_playwright() as pw:
            socket = (
                version(local_endpoint(args.endpoint))["webSocketDebuggerUrl"]
                if args.endpoint
                else start(pw, args.workspace)["websocket"]
            )
            browser = pw.chromium.connect_over_cdp(socket)
            if not browser.contexts:
                raise RuntimeError("CDP browser has no persistent context")
            for job in jobs:
                record = process(
                    browser.contexts[0],
                    store,
                    job,
                    profile,
                    bank,
                    args.submit,
                    args.model if args.ai else None,
                )
                print(
                    json.dumps(
                        {
                            "job_key": job_key(job),
                            "status": record["status"],
                            "blockers": record.get("blockers", []),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            # Do not browser.close(): Chromium owns the unresolved tabs beyond this process.
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        parser.exit(2, "jobs-gpt: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
