"""Failure-injection and privacy regressions from the hardening audit."""

import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from test_workflow import URL, Workspace

from jobs_gpt import forms
from jobs_gpt.browser import local_endpoint, start
from jobs_gpt.models import eligibility_issues, job_key
from jobs_gpt.privacy import private_path, write_private
from jobs_gpt.runner import process
from jobs_gpt.state import Store


class StorageHardeningTests(Workspace):
    def test_copied_job_review_and_replaced_resume_are_rejected(self):
        different = dict(self.job, url=URL.replace("89abc", "89abd"))
        self.assertTrue(eligibility_issues(different, self.profile))
        changed = dict(self.profile, _resume_sha256="replacement")
        self.assertTrue(eligibility_issues(self.job, changed))

    def test_only_ready_can_record_intent(self):
        key = job_key(self.job)
        for status in ("preview", "needs_review", "drafted", "validation_rejected"):
            self.store.put(key, {"status": status})
            self.assertFalse(self.store.begin_attempt(key, {"status": "ready"}))
        with self.assertRaises(ValueError):
            self.store.put(key, {"status": "submitted"})

    def test_submission_evidence_survives_partial_updates(self):
        key = job_key(self.job)
        ready = {"status": "ready", "confirmation_before": []}
        self.store.put(key, ready)
        self.store.begin_attempt(key, ready)
        self.store.outcome(
            key,
            {"status": "submitted", "confirmation": "received"},
            {"receipt": "received"},
        )
        self.store.put(
            key,
            {"status": "submitted", "confirmation": None, "tab_close_error": "Closed"},
        )
        self.assertEqual(self.store.get(key)["confirmation"], "received")
        self.assertIn("submit_attempt_at", self.store.get(key))

    def test_db_connections_are_closed(self):
        with self.store.connect() as con:
            con.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")

    def test_atomic_private_write_and_failed_serialization(self):
        path = self.root / "private" / "bank.json"
        write_private(path, {"existing": True})
        with self.assertRaises(ValueError):
            write_private(path, {"bad": float("nan")})
        self.assertEqual(json.loads(path.read_text()), {"existing": True})
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_git_checkout_and_symlink_are_not_private_storage(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        with self.assertRaises(ValueError):
            Store(repo / "private")
        with self.assertRaises(ValueError):
            write_private(repo / "bank.json", {})
        alias = self.root / "alias"
        alias.symlink_to(repo, target_is_directory=True)
        with self.assertRaises(ValueError):
            private_path(alias / "resume.pdf")

    def test_endpoint_reuse_requires_same_browser_identity(self):
        root = self.root / "browser"
        root.mkdir(mode=0o700)
        write_private(
            root / "browser.json",
            {
                "endpoint": "http://127.0.0.1:9333",
                "profile": str(root / "browser-profile"),
                "websocket": "ws://127.0.0.1:9333/devtools/browser/original",
            },
        )
        with patch(
            "jobs_gpt.browser.version",
            return_value={
                "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/other"
            },
        ):
            with self.assertRaises(RuntimeError):
                start(None, root)
        for endpoint in (
            "http://127.0.0.1:80/path",
            "http://127.0.0.1:80?x=1",
            "http://remote.test:80",
            "http://localhost",
        ):
            with self.assertRaises(ValueError):
                local_endpoint(endpoint)

    def test_workspace_rejects_permissive_or_symlinked_ledger(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions")
        permissive = self.root / "permissive"
        permissive.mkdir(mode=0o755)
        permissive.chmod(0o755)
        with self.assertRaises(ValueError):
            Store(permissive)
        root = self.root / "symlinked"
        root.mkdir(mode=0o700)
        (root / "applications.sqlite3").symlink_to(self.store.path)
        with self.assertRaises(ValueError):
            Store(root)

    def test_review_and_doctor_cli_roundtrip(self):
        import io
        from contextlib import redirect_stdout

        from jobs_gpt.cli import main

        jobs = self.root / "jobs.json"
        write_private(jobs, [dict(self.job, eligibility={"verified": False})])
        with redirect_stdout(io.StringIO()):
            main(
                [
                    "review-eligibility",
                    "--jobs",
                    str(jobs),
                    "--profile",
                    str(self.profile_path),
                    "--job-key",
                    job_key(self.job),
                    "--evidence",
                    "Actual synthetic fixture evidence",
                    "--source-url",
                    URL,
                    "--reviewer",
                    "offline tester",
                ]
            )
        reviewed = json.loads(jobs.read_text())[0]
        self.assertEqual(eligibility_issues(reviewed, self.profile), [])
        output = io.StringIO()
        with redirect_stdout(output):
            main(
                [
                    "--workspace",
                    str(self.store.root),
                    "doctor",
                    "--profile",
                    str(self.profile_path),
                ]
            )
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertNotIn("applicant@example.org", output.getvalue())

    def test_private_write_rejects_world_readable_directory(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions")
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "permissions 0700"):
            write_private(shared / "answers.json", {"private": "synthetic"})
        self.assertFalse((shared / "answers.json").exists())
        shared.chmod(0o700)
        write_private(shared / "answers.json", {"private": "synthetic"})
        self.assertEqual(
            json.loads((shared / "answers.json").read_text()),
            {"private": "synthetic"},
        )

    def test_eligibility_source_url_must_be_exact_job(self):
        good = dict(self.job, eligibility=dict(self.job["eligibility"]))
        self.assertEqual(eligibility_issues(good, self.profile), [])
        for bad_url in (
            URL.replace("89abc", "89abd"),
            "https://jobs.lever.co/other/12345678-1234-1234-1234-123456789abc",
            "https://employer.example/careers/another-position",
            "http://jobs.ashbyhq.com/fixture/12345678-1234-1234-1234-123456789abc",
            "https://user:password@jobs.ashbyhq.com/fixture/12345678-1234-1234-1234-123456789abc",
        ):
            with self.subTest(bad_url=bad_url):
                bad = dict(self.job, eligibility=dict(self.job["eligibility"], source_url=bad_url))
                self.assertIn(
                    "Eligibility source URL must match",
                    eligibility_issues(bad, self.profile)[0],
                )

    def test_review_cli_refuses_unrelated_evidence_source(self):
        from jobs_gpt.cli import main

        jobs = self.root / "review-jobs.json"
        write_private(jobs, [dict(self.job, eligibility={"verified": False})])
        with self.assertRaises(SystemExit) as captured:
            main([
                "review-eligibility",
                "--jobs", str(jobs),
                "--profile", str(self.profile_path),
                "--job-key", job_key(self.job),
                "--evidence", "Unrelated synthetic evidence",
                "--source-url", URL.replace("89abc", "89abd"),
                "--reviewer", "offline tester",
            ])
        self.assertEqual(captured.exception.code, 2)
        self.assertFalse(json.loads(jobs.read_text())[0]["eligibility"]["verified"])

    def test_installer_refuses_permissive_skill_destination(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions")
        import subprocess
        import sys

        script = Path(__file__).resolve().parents[1] / "scripts/install_skills.py"
        destination = self.root / "skills" / "job-applications"
        destination.mkdir(parents=True, mode=0o755)
        destination.chmod(0o755)
        proc = subprocess.run(
            [sys.executable, str(script), "--destination", str(self.root / "skills")],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("permissions 0700", proc.stderr)
        self.assertFalse((destination / "runtime.json").exists())

    def test_skill_refresh_preserves_custom_workspace(self):
        import subprocess
        import sys

        script = Path(__file__).resolve().parents[1] / "scripts/install_skills.py"
        destination = self.root / "skills"
        custom = self.root / "custom-workspace"
        command = [sys.executable, str(script), "--destination", str(destination)]
        subprocess.run(
            command + ["--workspace", str(custom)], check=True, capture_output=True
        )
        subprocess.run(command, check=True, capture_output=True)
        for name in ("job-applications", "jobs-gpt-maintenance"):
            config = json.loads((destination / name / "runtime.json").read_text())
            self.assertEqual(config["workspace"], str(custom.resolve()))
            if os.name != "nt":
                self.assertEqual((destination / name).stat().st_mode & 0o777, 0o700)
                self.assertEqual(
                    (destination / name / "runtime.json").stat().st_mode & 0o777,
                    0o600,
                )

    def test_public_scan_checks_staged_blobs_including_ignored_files(self):
        import subprocess
        import sys

        repo = self.root / "scanrepo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"], cwd=repo, check=True, capture_output=True
        )
        (repo / ".gitignore").write_text("profile.json\n")
        (repo / "profile.json").write_text("{}")
        (repo / "source.py").write_text('token="' + "AIza" + "x" * 35 + '"')
        subprocess.run(
            ["git", "add", "-f", "profile.json", "source.py"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "source.py").write_text(
            "# cleaned worktree, staged secret must still be caught"
        )
        script = Path(__file__).resolve().parents[1] / "scripts/check_public_tree.py"
        result = subprocess.run(
            [sys.executable, str(script), "--index"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("private artifact", result.stdout)
        self.assertIn("credential-like content", result.stdout)
        self.assertNotIn("AIza" + "x" * 35, result.stdout)

    def test_secret_requests_do_not_follow_redirects(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.error import HTTPError

        from jobs_gpt.network import open_no_redirect

        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/sink")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError):
                open_no_redirect(
                    "http://127.0.0.1:" + str(server.server_port) + "/source"
                )
            self.assertEqual(requests, ["/source"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class BrowserHardeningTests(Workspace):
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
        self.html = ""
        self.context.route(
            "**/*",
            lambda route: route.fulfill(
                status=200, content_type="text/html", body=self.html
            ),
        )
        self.factory = patch("jobs_gpt.runner.page_for", return_value=self.page)
        self.factory.start()

    def tearDown(self):
        self.factory.stop()
        self.context.close()
        super().tearDown()

    def form(self):
        return """<form onsubmit="event.preventDefault();document.body.innerHTML='Your application has been received'"><label>Full name<input required></label><label>Email<input required type=email></label><label>Resume<input required type=file></label><button>Submit application</button></form>"""

    def phone_profile(self):
        from jobs_gpt.models import read_profile

        raw = json.loads(self.profile_path.read_text())
        raw["fields"]["phone"] = "+12025550123"
        write_private(self.profile_path, raw)
        self.profile = read_profile(self.profile_path)
        self.job["eligibility"]["profile_digest"] = self.profile["_profile_digest"]

    def test_phone_formatting_preserves_same_number(self):
        self.phone_profile()
        field = """<label>Phone<input type=tel oninput="this.value='+1 (202) 555-0123'"></label>"""
        self.html = self.form().replace("<button>", field + "<button>")
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "submitted", result)
        self.assertTrue(self.page.is_closed())

    def test_different_phone_digits_are_not_approved(self):
        self.phone_profile()
        field = '<label>Phone<input type=tel value="+1 (202) 555-0999"></label>'
        self.html = self.form().replace("<button>", field + "<button>")
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "needs_review", result)
        self.assertTrue(any("Phone" in b for b in result["blockers"]))
        self.assertFalse(self.page.is_closed())

    def test_receipt_like_instructions_do_not_confirm(self):
        for text in (
            "If your application has been received, wait.",
            "Example: Your application has been received",
            "Your application has been received?",
            "Your application has not been received",
        ):
            self.page.set_content("<p>" + text + "</p>")
            self.assertEqual(forms.confirmation(self.page.main_frame), [])
        self.page.set_content(
            "<h1>Your application has been received.</h1><p>We will contact you.</p>"
        )
        self.assertEqual(len(forms.confirmation(self.page.main_frame)), 1)

    def test_receipt_requires_application_controls_to_disappear(self):
        self.page.set_content(
            "<h1>Your application has been received</h1><label>Email<input type=email></label>"
        )
        self.assertFalse(forms.receipt_ready(self.page.main_frame))

    def test_close_failure_cannot_downgrade_success(self):
        self.html = self.form()
        with patch.object(
            self.page, "close", side_effect=RuntimeError("synthetic close failure")
        ):
            result = process(self.context, self.store, self.job, self.profile, [], True)
            self.assertEqual(result["status"], "submitted", result)
            self.assertEqual(
                process(self.context, self.store, self.job, self.profile, [], True)[
                    "status"
                ],
                "submitted",
            )
        self.assertIn("confirmation", self.store.get(job_key(self.job)))
        self.assertFalse(self.page.is_closed())

    def test_unknown_legacy_attempt_is_not_confirmed_from_unrelated_text(self):
        self.html = "<h1>Your application has been received</h1>"
        self.store.import_record(
            job_key(self.job), {"status": "unconfirmed", "job": self.job}
        )
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertFalse(self.page.is_closed())

    def test_baseline_receipt_is_not_reused_after_crash(self):
        self.html = "<h1>Your application has been received</h1>"
        self.store.import_record(
            job_key(self.job),
            {
                "status": "unconfirmed",
                "job": self.job,
                "confirmation_before": ["Your application has been received"],
            },
        )
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertFalse(self.page.is_closed())

    def test_late_receipt_after_captcha_is_recovered_without_click(self):
        self.html = "<h1>Your application has been received</h1>"
        self.store.import_record(
            job_key(self.job),
            {
                "status": "captcha_blocked",
                "job": self.job,
                "confirmation_before": [],
                "submit_attempt_at": "prior intent",
            },
        )
        result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(self.page.is_closed())

    def test_field_change_during_screenshot_blocks_final_click(self):
        self.html = self.form()

        def changed(*args):
            self.page.get_by_label("Email").fill("unexpected@example.org")
            return None

        with patch("jobs_gpt.runner.screenshot", side_effect=changed):
            result = process(self.context, self.store, self.job, self.profile, [], True)
        self.assertEqual(result["status"], "needs_review")
        self.assertFalse(self.page.is_closed())
        self.assertNotIn("submit_attempt_at", self.store.get(job_key(self.job)))


if __name__ == "__main__":
    unittest.main()
