# jobs-gpt

Playwright application automation for Ashby, Greenhouse (including hosted embeds), and Lever. The runner fills verified contact details, uploads the résumé, selects exact form options, and submits eligible forms when explicitly requested. Complex answers can be drafted with Gemini using the résumé PDF, then independently reviewed against the sources.

Confirmed applications close after a fresh employer receipt is saved. Blocked or uncertain applications stay open in an OS-owned browser, including after the Python process exits. The browser starts in the background on macOS; desktop focus behavior on other systems depends on the window manager.

This is a tested initial release, not a guarantee that every customized ATS form works. Unknown fields, custom uploads, unsupported controls, image CAPTCHAs, location restrictions and employer application limits stop submission and remain reviewable. Extending an adapter is preferable to guessing.

## Install

Requires Python 3.9+ and Chromium. Keep a single private workspace for each applicant, outside this repository.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install chromium
```

The commands below assume `jobs-gpt` is on PATH (activate the virtual environment, or use `.venv/bin/jobs-gpt`). Copy `examples/profile.example.json` to your private directory, replace example values, and place your actual PDF beside it. `resume_file` resolves relative to the profile. The example email cannot be used with `--submit`. `fields` contains contact values; `facts` and `preferences` contain only explicit applicant statements. No personal defaults are bundled.

## Find candidates

```bash
jobs-gpt discover --boards examples/boards.example.json --days 10 --output /private/jobs.json
```

Configure your own board list. Ashby and Greenhouse discovery uses employer GET feeds; it never approves eligibility. Results are filtered by title and remote indicators, deduplicated by ATS/board/job identity, and accompanied by an audit file. Ashby `publishedAt` means last publication and may reflect republication. Greenhouse jobs without `first_published` are excluded: `updated_at` is never substituted. Remote indicators do not establish country or time-zone eligibility. Discovery does not promise a requested number of suitable jobs.

Use direct HTTPS ATS URLs. Custom employer URLs must be resolved to a supported posting before adding them. Lever direct application URLs work with the runner; its discovery feed is not implemented.

## Verify eligibility and prepare forms

```bash
jobs-gpt fingerprint --profile /private/profile.json
jobs-gpt --workspace /private/state browser
jobs-gpt --workspace /private/state run --jobs /private/jobs.json --profile /private/profile.json
```

Run `jobs-gpt --workspace /private/state doctor --profile /private/profile.json` for a read-only preflight. Add `--browser` only when a workspace browser is already running; it checks identity without opening tabs.

After reviewing the live job against the applicant's actual location, work authorization, qualifications and preferences, add an eligibility object to that job:

```json
{
  "verified": true,
  "job_key": "ats:board:posting-id",
  "resume_sha256": "_resume_sha256 from fingerprint",
  "reviewed_by": "reviewing human or agent",
  "evidence": "Specific location/qualification evidence from the posting and applicant facts",
  "source_url": "https://the.actual.job/posting",
  "checked_at": "current ISO timestamp with time zone",
  "profile_digest": "_profile_digest from fingerprint"
}
```

Prefer recording the review with the CLI, which binds it to the correct job and source fingerprints:

```bash
jobs-gpt review-eligibility --jobs /private/jobs.json --profile /private/profile.json \
  --job-key 'ats:board:posting-id' --evidence 'Actual reviewed eligibility evidence' \
  --source-url 'https://the.actual.job/posting' --reviewer 'Reviewer'
```

This records your review; it does not perform or certify the reasoning for you. The `source_url` must identify the **same direct HTTPS ATS posting** as the job URL; a generic employer page or another posting is not accepted as eligibility evidence. Existing reviews with mismatched URLs require fresh review. Old eligibility entries missing the job/PDF bindings need a fresh review.

Eligibility reviews expire after 24 hours and are invalidated by profile changes. The script checks review completeness/freshness, not the reviewer's reasoning. A competent human or agent must actually verify the evidence. Jobs remain open when eligibility is unverified. JSON jobs require `company`, `role`, `url`; the other discovery fields are evidence for that review.

## Gemini and source review

Set `GEMINI_API_KEY` and `GEMINI_MODEL` in the local process environment (or obtain the key through a hidden prompt in your invoking tool). Do not put keys in command arguments, source, reports or Git. `.env.example` documents names only; dotenv files are not automatically loaded. Choose a model accessible to your account; no model ID is hardcoded.

```bash
jobs-gpt --workspace /private/state run --jobs /private/jobs.json --profile /private/profile.json --ai --model YOUR_MODEL
jobs-gpt --workspace /private/state report --output /private/report.json
```

Gemini receives the actual PDF, applicant profile, job identity and visible form context. API calls use an API-key header, structured JSON, bounded retries, and a PDF size limit. Inference failures leave the form blocked. Model answers are saved as drafts; the model cannot mark its own answer verified or control the browser.

Independently compare every draft with the original résumé/profile. An agent can do this review under the user's existing authorization. For an answer supported by an exact source quote:

```bash
jobs-gpt approve --jobs /private/jobs.json --profile /private/profile.json \
  --answers /private/answers.json --job-key 'ats:board:posting-id' \
  --question 'Exact field question' --answer-json '"Supported answer"' \
  --source-kind resume --quote 'Exact supporting source quote' --reviewer 'Reviewer'
```

`source-kind` is `resume` or `profile`. For facts learned directly from the user, add their exact statement to the private profile first. Answers can be JSON strings, booleans or arrays as appropriate to the control. Quote matching is an integrity check, not a semantic verifier: reviewers must check that the quote supports **every** claim. Answer approvals bind to a job, exact normalized question, profile hash, PDF hash and approval-content hash. Changing an answer/source invalidates approval; re-review it.

## Submit and resume

```bash
jobs-gpt --workspace /private/state run --jobs /private/jobs.json \
  --profile /private/profile.json --answers /private/answers.json --submit
```

The final click requires verified eligibility, verified current values and PDF bytes, no unresolved required fields or visible errors, and exactly one enabled submit control. A SQLite transaction records intent **before** clicking. A fresh standalone receipt plus disappearance of the submit and contact/upload controls confirms success; a screenshot and receipt are retained before closing the tab. An HTTP 200 or click alone is insufficient.

Unconfirmed attempts, crashes after intent, CAPTCHAs and employer frequency limits are protected from automatic retry. Re-running inspects protected tabs without editing/clicking. Automatic recovery compares the receipt with its persisted pre-submit baseline; legacy unknown attempts without a baseline require manual reconciliation. A tab-close failure records a cleanup error and preserves the confirmed receipt. Completing a CAPTCHA may itself send the application: inspect for a receipt first. The initial release deliberately has no generic override to retry protected records. Resolve these individually with employer evidence; retain the original ledger/event history. Do not delete state to evade limits or retry uncertainty.

`--only 'ats:board:posting-id'` targets one job. `--endpoint http://127.0.0.1:PORT` attaches to an explicitly chosen existing Chromium CDP browser. Prefer the dedicated workspace browser, never your daily browsing profile. CDP is loopback-only; treat the browser profile as sensitive. Workspace metadata pins its debugging socket identity, so an unrelated browser on a recycled port is rejected. An old metadata file without an identity needs explicit inspection/attachment; it is never silently trusted. Keep one controller/workspace; workspace locks and atomic submission intents prevent local duplicate clicks. Separate workspaces cannot deduplicate each other.

The runner disconnects without closing Chromium. To restore after an OS/browser restart, rerun: previously unresolved jobs open new background tabs while submission uncertainty stays protected. Existing unknown answers are never automatically trusted.

## Migrate a private pilot

```bash
jobs-gpt --workspace /private/state import-legacy --tracker /private/application-results.json
```

Imports supported ATS identities without overwriting existing records. Confirmed receipts and uncertain attempts remain protected. Legacy target-ID fields may differ, so unresolved forms can reopen; importing does not submit anything. Review the import report before using `--submit`.

## Development and validation

```bash
python -m unittest discover -s tests -v
```

Tests route synthetic HTTPS ATS pages locally. No employer applications or Gemini inference are performed by CI. Coverage includes résumé byte verification, approved/unknown screening answers, receipt-only closure, CAPTCHA/limit stops, uncertain retry protection, duplicate-intent transactions, review invalidation and publication-date filtering. Add a realistic local fixture when changing a selector/control; never use real applicant data in fixtures. Generic controls are intentionally conservative.

The reusable Codex skill is in `skills/job-applications/`. Install both reusable skills and machine-local path hints with:

```bash
.venv/bin/python scripts/install_skills.py --workspace /private/state
```

Invoke `$job-applications` to operate applications, or `$jobs-gpt-maintenance` to audit/repair the scripts and skills. The application skill includes a resolver (`scripts/jobs_gpt.py`) for environments where the CLI is not on PATH. The local-only `runtime.json` stores checkout/workspace paths; it never contains keys or applicant facts. Keep the package installed or the checkout discoverable. The skill contains no applicant secrets or machine-specific paths.

See [hardening findings and validation](docs/HARDENING.md) for the audit scope and remaining limits.

Private data belongs outside Git. Private runtime inputs and outputs are rejected inside Git checkouts. New workspaces and private-output directories use mode 0700 and the ledger, answer bank and metadata use 0600 on POSIX systems. Writes to already-existing directories with group/other permissions are refused; correct their permissions explicitly before writing. The script never silently changes permissions of an arbitrary existing directory. The skill installer creates each skill directory with private permissions before writing local runtime hints; older permissive installations must be corrected explicitly. JSON updates use a temporary file and atomic replacement, so a failed write preserves the previous record.  The ignore rules are a second line of defense, not a substitute for inspecting `git diff --cached` before publishing. Reports and screenshots contain personal information. This repository intentionally contains no applicant profile, résumé, key, browser storage or live application ledger.

## Upstream references

- [Ashby public job postings](https://developers.ashbyhq.com/docs/public-job-posting-api)
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
- [Gemini document processing](https://ai.google.dev/gemini-api/docs/document-processing)
- [Playwright CDP connection](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
