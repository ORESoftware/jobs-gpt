# Hardening audit — 2026-11-10

Scope: the generic runner at baseline `7df09dbb299f777799e1fac96a30bed807b51ea1`, its local state, form adapters, Gemini boundary, browser lifetime, CLI, tests and reusable skills. Release 0.2.0 requires refreshed eligibility reviews with job/PDF bindings and a mode-0700 private workspace; it does not rewrite existing submission records. Maintenance validation uses synthetic applicants and locally routed HTTPS pages; it does not send employer applications or consume Gemini inference.

## Findings addressed

| Finding | Consequence before fix | Result and regression evidence |
|---|---|---|
| Eligibility review bound only to profile | A copied review could approve another job or a replaced PDF | Require exact ATS job key and résumé hash as well as profile hash/freshness; `review-eligibility` records these after actual review |
| Receipt search accepted substrings | Receipt-like instructions in longer sentences could look like acceptance | Match standalone receipt sentences and require contact/upload controls and submit controls to disappear |
| Recovery lacked a persisted receipt baseline | A stale receipt-like message could resolve uncertainty | Save the pre-click baseline; recover only a new receipt, including eligible embedded forms and post-submit CAPTCHA outcomes; legacy uncertainty stays protected |
| Tab-close exception entered submission failure handling | A saved success could trigger a downgrade attempt and stop the batch | Isolate cleanup and record its error while preserving the confirmed receipt |
| Screenshot delay came after final value checks | A user/page edit during a slow screenshot could escape final validation | Perform screenshot before the final value/eligibility checks; failure injection changes the email during screenshot and proves no click occurs |
| State API accepted premature intents or direct final states | An internal caller could bypass the ready state or discard evidence | Only ready records can acquire an intent; terminal states require intent/outcome APIs; protected receipt/baseline fields survive partial updates |
| SQLite connections relied on garbage collection | Long runs could accumulate connections and locks | Explicitly close every connection after commit/rollback; regression checks the connection is unusable outside its scope |
| Private output used ordinary writes and could enter Git | Partial writes or accidental publication of applicant data | Reject private artifacts inside Git (including resolved symlinks), atomically replace JSON using private temporary files, require private workspace permissions and use a private ledger; reject ledger/lock symlinks |
| A reused CDP port was trusted | An unrelated browser could be mistaken for the workspace browser | Store and compare the debugging socket identity and expected profile; reject remote, path/query-bearing or redirecting endpoints |
| HTTP clients could follow redirects | API key headers/local debugging requests could cross a redirect | Disable redirects for Gemini and CDP requests; a local redirect server proves the sink is never requested |
| Skill reinstall overwrote a custom workspace | Future runs could lose the intended applicant ledger | Preserve the existing workspace hint unless `--workspace` explicitly replaces it; isolated reinstall regression |
| Phone formatter changed presentation | An unchanged number was rejected before digit-normalization | Share normalized telephone comparison between trust revalidation and blockers; verify different digits still block |
| Operational knowledge existed mainly in chat | Future agents could lose state locations or repeat unsafe repairs | Maintain application/maintenance skills, a local path-only runtime hint, repository instructions and read-only `doctor` diagnostics |

## Validation

`tests/test_workflow.py` exercises core fixtures for Ashby, Greenhouse and Lever identities, required fields, résumé bytes, exact dropdown options, source-bound reviews, publication-date semantics, uncertain attempts and receipt-only tab closure. `tests/test_hardening.py` injects storage, browser, receipt, cleanup and concurrent-edit failures. The full offline suite contains 43 tests and runs in CI on Python 3.9 and 3.12 using Ubuntu 24.04.

The skill-creator validator checks skill metadata. Independent forward tests use isolated synthetic workspaces and may identify additional behavioral defects; meaningful findings get their own fixture before publication. `doctor` checks dependency versions, private-directory permissions, ledger integrity/status consistency and optional local browser identity without submitting forms.

## Material limits

- Source hashes and quote matching are integrity checks, not semantic proof. A human or agent must independently review eligibility and every answer claim against original evidence. The script cannot certify that a reviewer reasoned correctly. Approval digests are not signatures against hostile local file edits.
- ATS layouts, receipt wording and custom widgets change. Unsupported or ambiguous forms stay open. This is conservative support for the named adapters, not universal automation.
- Intent precedes a browser click, and timeouts can leave an uncertain outcome. Exactly-once delivery to an employer cannot be guaranteed by browser automation. The ledger prevents automatic retries, rather than assuming rejection. Legacy uncertain records without a baseline require manual reconciliation.
- Final checks minimize exposure to concurrent edits but do not make a remote page transactional. Avoid editing a form while its controller is submitting it.
- Private files contain applicant information. Keep one durable workspace per applicant. Independent workspaces cannot deduplicate each other, and local filesystem access by the same user is outside the integrity guarantees.
- The browser runs in the background on macOS. Other desktops control their own focus policy. Do not use native activation to work around a blocked form.
- No image CAPTCHA solving, employer limit evasion, or unverified screening defaults are provided. A maintenance task does not authorize new applications.

## Follow-up hardening — 2026-11-14

- **Private output directories:** Mode 0700 is required for both newly created and pre-existing POSIX directories used for applicant artifacts, including skill runtime hints. Existing shared directories are rejected rather than silently chmod'ed. Files remain atomically replaced with private temporary files.
- **Eligibility source identity:** Both `review-eligibility` and submission-time `eligibility_issues` require the evidence URL to resolve to the *same* direct HTTPS ATS board/posting identity as the intended job. A generic employer site or unrelated posting cannot be used to bless another job. Older review entries with mismatched sources require a fresh manual review.
- **Offline regressions:** Test permissive-directory rejection and correction, wrong ATS/job/source URLs, rejected CLI review without modifying the jobs file, and safe behavior for pre-existing permissive skill destinations. No employer application or Gemini inference is performed.
