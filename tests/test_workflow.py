"""Offline browser integrations: no employer navigation or real submissions."""

import datetime as dt
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

from jobs_gpt import forms
from jobs_gpt.discovery import collect
from jobs_gpt.gemini import request_answers
from jobs_gpt.models import (
    digest,
    eligibility_issues,
    identity,
    job_key,
    now,
    read_profile,
    validate_real_applicant,
)
from jobs_gpt.reviews import approve, approved
from jobs_gpt.runner import process
from jobs_gpt.state import Store, workspace_lock

URL = (
    "https://jobs.ashbyhq.com/fixture/12345678-1234-1234-1234-123456789abc/application"
)
JOB = {"company": "Fixture Employer", "role": "Software Engineer", "url": URL}


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        writer = PdfWriter()
        writer.add_blank_page(width=600, height=800)
        with (self.root / "resume.pdf").open("wb") as handle:
            writer.write(handle)
        self.profile_path = self.root / "profile.json"
        self.profile_path.write_text(
            json.dumps(
                {
                    "fields": {
                        "full_name": "Fixture Applicant",
                        "email": "applicant@example.org",
                    },
                    "resume_file": "resume.pdf",
                    "facts": {"runtime": "Built compiler tooling in Rust."},
                }
            )
        )
        self.profile = read_profile(self.profile_path)
        self.store = Store(self.root / "state")
        self.job = dict(JOB)
        self.job["eligibility"] = {
            "verified": True,
            "job_key": job_key(self.job),
            "resume_sha256": self.profile["_resume_sha256"],
            "reviewed_by": "offline reviewer",
            "evidence": "Synthetic test eligibility",
            "source_url": URL,
            "profile_digest": self.profile["_profile_digest"],
            "checked_at": now(),
        }

    def tearDown(self):
        self.temp.cleanup()


class GuardTests(Workspace):
    def test_ats_identity_and_redirect_guards(self):
        self.assertEqual(
            identity(URL), identity(URL.replace("/application", "?source=abc"))
        )
        self.assertEqual(
            identity("https://boards.greenhouse.io/embed/job_app?for=fixture&token=42"),
            ("greenhouse", "fixture", "42"),
        )
        self.assertEqual(
            identity(
                "https://jobs.lever.co/fixture/12345678-1234-1234-1234-123456789abc/apply"
            )[0],
            "lever",
        )
        for url in [
            "http://jobs.ashbyhq.com/fixture/42",
            URL.replace("ashbyhq.com", "ashbyhq.com.evil.test"),
            URL.replace("https://", "https://secret@"),
            "https://jobs.ashbyhq.com/fixture",
        ]:
            with self.assertRaises(ValueError):
                identity(url)

    def test_placeholder_cannot_submit(self):
        with self.assertRaises(ValueError):
            validate_real_applicant(self.profile)

    def test_eligibility_expires_and_binds_profile(self):
        self.assertEqual(eligibility_issues(self.job, self.profile), [])
        self.job["eligibility"]["checked_at"] = "2000-01-01T00:00:00+00:00"
        self.assertTrue(eligibility_issues(self.job, self.profile))
        self.job["eligibility"]["checked_at"] = now()
        self.job["eligibility"]["profile_digest"] = "other"
        self.assertTrue(eligibility_issues(self.job, self.profile))

    def test_review_is_scoped_and_invalidated_by_changes(self):
        path = self.root / "answers.json"
        entry = approve(
            path,
            self.profile,
            self.job,
            "Describe your compiler work",
            "Built compiler tooling in Rust.",
            "profile",
            "Built compiler tooling in Rust.",
            "independent reviewer",
        )
        self.assertEqual(len(approved([entry], self.profile, self.job)), 1)
        changed = dict(entry, answer="Invented five years")
        self.assertEqual(approved([changed], self.profile, self.job), [])
        other = dict(self.job, url=URL.replace("89abc", "89abd"))
        self.assertEqual(approved([entry], self.profile, other), [])
        profile = dict(self.profile, _resume_sha256="changed")
        self.assertEqual(approved([entry], profile, self.job), [])
        with self.assertRaises(ValueError):
            approve(
                path,
                self.profile,
                self.job,
                "Years?",
                "5",
                "profile",
                "five years",
                "reviewer",
            )

    def test_atomic_intent_and_crash_protection(self):
        key = job_key(self.job)
        record = {"status": "ready", "job": self.job}
        self.store.put(key, record)
        self.assertTrue(self.store.begin_attempt(key, record))
        self.assertFalse(Store(self.store.root).begin_attempt(key, record))
        with self.assertRaises(ValueError):
            self.store.put(key, record)
        self.store.outcome(
            key, dict(record, status="unconfirmed"), {"observation": "connection lost"}
        )
        self.assertFalse(self.store.begin_attempt(key, record))
        self.store.outcome(
            key, dict(record, status="submitted"), {"receipt": "received"}
        )
        with self.assertRaises(ValueError):
            self.store.outcome(key, dict(record, status="unconfirmed"), {"x": 1})

    def test_lock(self):
        with workspace_lock(self.root):
            with self.assertRaises(RuntimeError):
                with workspace_lock(self.root):
                    pass

    def test_discovery_uses_publication_not_update(self):
        current = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc)
        feed = {
            "jobs": [
                {
                    "id": 42,
                    "title": "Remote Engineer",
                    "location": {"name": "Remote"},
                    "absolute_url": "https://boards.greenhouse.io/fixture/jobs/42",
                    "updated_at": "2026-11-09T00:00:00Z",
                },
                {
                    "id": 43,
                    "title": "Remote Engineer",
                    "location": {"name": "Remote"},
                    "absolute_url": "https://boards.greenhouse.io/fixture/jobs/43",
                    "first_published": "2026-11-08T00:00:00Z",
                },
                {
                    "id": 44,
                    "title": "Remote Engineer",
                    "location": {"name": "Remote"},
                    "absolute_url": "https://boards.greenhouse.io/fixture/jobs/44",
                    "first_published": "2020-01-01T00:00:00Z",
                },
            ]
        }
        result = collect(
            [{"ats": "greenhouse", "board": "fixture"}],
            loader=lambda _: feed,
            clock=current,
        )
        self.assertEqual(
            [j["url"] for j in result["jobs"]],
            ["https://boards.greenhouse.io/fixture/jobs/43"],
        )
        self.assertFalse(result["jobs"][0]["eligibility"]["verified"])
        self.assertEqual(len(result["errors"]), 1)

    def test_gemini_inline_pdf_and_secret_transport(self):
        seen = {}

        @contextmanager
        def opener(req, timeout):
            seen["request"] = req
            import io

            yield io.StringIO(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "finishReason": "STOP",
                                "content": {"parts": [{"text": '{"answers": []}'}]},
                            }
                        ]
                    }
                )
            )

        with patch.dict("os.environ", {"GEMINI_API_KEY": "fixture-secret"}):
            self.assertEqual(
                request_answers(
                    {},
                    Path(self.profile["_resume_path"]),
                    self.job,
                    [],
                    "posting",
                    "configured-model",
                    opener=opener,
                ),
                [],
            )
        self.assertNotIn("fixture-secret", seen["request"].full_url)
        payload = json.loads(seen["request"].data)
        self.assertEqual(
            payload["contents"][0]["parts"][0]["inlineData"]["mimeType"],
            "application/pdf",
        )
        self.assertIn(
            "not instructions", payload["systemInstruction"]["parts"][0]["text"]
        )


class BrowserTests(Workspace):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        super().setUp()
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.context.route(
            "**/*",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body=self.html
            ),
        )
        self.html = ""
        self.patches = [patch("jobs_gpt.runner.page_for", return_value=self.page)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.context.close()
        super().tearDown()

    def form(self, extra="", outcome="receipt"):
        handler = (
            "document.body.innerHTML='Your application has been received'"
            if outcome == "receipt"
            else "document.querySelector('#pending').textContent='Processing'"
        )
        return (
            """<form onsubmit="event.preventDefault();%s"><label>Full name<input required name=name></label><label>Email<input type=email required></label><label>Resume<input type=file required></label>%s<button type=submit>Submit application</button></form><div id=pending></div>"""
            % (handler, extra)
        )

    def test_success_fills_uploads_receipt_and_closes(self):
        self.html = self.form()
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "submitted", result)
        self.assertTrue(self.page.is_closed())
        self.assertTrue(Path(result["screenshot"]).is_file())
        result2 = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result2["status"], "submitted")

    def test_unknown_required_question_blocks_and_keeps_tab(self):
        self.html = self.form(
            "<label>Are you willing to relocate?<input required></label>"
        )
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(self.page.is_closed())
        self.assertEqual(
            self.page.get_by_label("Are you willing to relocate?").input_value(), ""
        )

    def test_unapproved_prefilled_answer_blocks(self):
        self.html = self.form(
            '<label>Years of compiler experience<input required value="99"></label>'
        )
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(self.page.is_closed())
        self.assertTrue(any("Review existing" in s for s in result["blockers"]))

    def test_reviewed_answer_submits(self):
        entry = approve(
            self.root / "answers.json",
            self.profile,
            self.job,
            "Describe your compiler work",
            "Built compiler tooling in Rust.",
            "profile",
            "Built compiler tooling in Rust.",
            "test reviewer",
        )
        self.html = self.form(
            "<label>Describe your compiler work<textarea required></textarea></label>"
        )
        result = process(
            self.context, self.store, self.job, self.profile, [entry], True
        )
        self.assertEqual(result["status"], "submitted", result)

    def test_preview_never_clicks(self):
        self.html = self.form()
        result = process(self.context, self.store, self.job, self.profile, [], False)
        self.assertEqual(result["status"], "preview")
        self.assertFalse(self.page.is_closed())

    def test_unverified_location_never_submits(self):
        self.job["eligibility"] = {"verified": False}
        self.html = self.form()
        self.assertEqual(
            process(self.context, self.store, self.job, self.profile, [], True)[
                "status"
            ],
            "needs_review",
        )
        self.assertFalse(self.page.is_closed())

    def test_no_receipt_is_protected_from_retry(self):
        self.html = self.form(outcome="unknown")
        with patch("jobs_gpt.runner.forms.confirmation_after", return_value=None):
            result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertFalse(self.page.is_closed())
        self.page.evaluate(
            "document.querySelector('button').addEventListener('click',()=>window.retried=true)"
        )
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIsNone(self.page.evaluate("window.retried"))

    def test_captcha_detected_even_when_form_hidden(self):
        self.html = (
            "<div aria-hidden=true>"
            + self.form()
            + '</div><iframe src="https://captcha.test/recaptcha/bframe"></iframe>'
        )
        self.assertEqual(
            process(self.context, self.store, self.job, self.profile, [], True)[
                "status"
            ],
            "captcha_blocked",
        )
        self.assertFalse(self.page.is_closed())

    def test_limit_respected(self):
        self.html = (
            "<div role=alert>You have reached the application limit</div>" + self.form()
        )
        self.assertEqual(
            process(self.context, self.store, self.job, self.profile, [], True)[
                "status"
            ],
            "application_limit",
        )
        self.assertFalse(self.page.is_closed())

    def test_real_dropdown_option_required(self):
        self.html = '<label>Notice period<select required><option value="">Choose</option><option value="two">Two weeks</option></select></label>'
        self.page.goto(URL)
        field = forms.discover(self.page.main_frame)[0]
        forms.set_value(self.page.main_frame, field, "Two weeks")
        self.assertEqual(self.page.get_by_label("Notice period").input_value(), "two")

    def test_source_reviewed_native_select_uses_visible_text(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                if self.page.is_closed():
                    self.page = self.context.new_page()
                # Use a distinct identity/ledger per successful form.
                job = dict(
                    self.job, url=URL.replace("89abc", "89ab1" if nested else "89ab2")
                )
                job["eligibility"] = dict(
                    job["eligibility"], job_key=job_key(job), source_url=job["url"]
                )
                self.job = job
                self.profile["facts"] = {"notice": "Two weeks"}
                self.profile["_profile_digest"] = digest(
                    {k: v for k, v in self.profile.items() if not k.startswith("_")}
                )
                job["eligibility"]["profile_digest"] = self.profile["_profile_digest"]
                entry = approve(
                    self.root / "answers.json",
                    self.profile,
                    job,
                    "Notice period",
                    "Two weeks",
                    "profile",
                    "Two weeks",
                    "test reviewer",
                )
                select = '<select id=notice required><option value="">Choose</option><option value=two>Two weeks</option></select>'
                extra = (
                    "<label>Notice period" + select + "</label>"
                    if nested
                    else "<label for=notice>Notice period</label>" + select
                )
                self.html = self.form(extra)
                # Override the page factory for this subcase after closing the earlier page.
                with patch("jobs_gpt.runner.page_for", return_value=self.page):
                    result = process(
                        self.context, self.store, job, self.profile, [entry], True
                    )
                self.assertEqual(result["status"], "submitted", result)
                self.assertTrue(self.page.is_closed())

    def test_greenhouse_and_lever_form_paths(self):
        for url in (
            "https://job-boards.greenhouse.io/fixture/jobs/42",
            "https://jobs.lever.co/fixture/12345678-1234-1234-1234-123456789abc/apply",
        ):
            with self.subTest(url=url):
                page = self.context.new_page()
                self.html = self.form()
                job = dict(self.job, url=url)
                job["eligibility"] = dict(
                    job["eligibility"], job_key=job_key(job), source_url=job["url"]
                )
                with patch("jobs_gpt.runner.page_for", return_value=page):
                    result = process(
                        self.context, self.store, job, self.profile, [], True
                    )
                self.assertEqual(result["status"], "submitted", result)
                self.assertTrue(page.is_closed())

    def test_typed_autocomplete_is_not_a_selected_option(self):
        self.html = '<label for=loc>Location</label><input id=loc role=combobox value="United States">'
        self.page.goto(URL)
        fields = forms.discover(self.page.main_frame)
        self.assertFalse(fields[0]["selection_verified"])
        issues = forms.blockers(
            self.page.main_frame,
            [
                {
                    "field": "Location",
                    "field_id": fields[0]["id"],
                    "value": "United States",
                    "source": "verified_answer",
                }
            ],
        )
        self.assertIn("Dropdown choice not verified: Location", issues)

    def test_wrong_resume_does_not_become_trusted(self):
        self.html = self.form()
        self.page.goto(URL)
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        wrong = self.root / "wrong.pdf"
        with wrong.open("wb") as handle:
            writer.write(handle)
        self.page.get_by_label("Resume").set_input_files(str(wrong))
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(self.page.is_closed())


if __name__ == "__main__":
    unittest.main()
