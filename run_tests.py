#!/usr/bin/env python3
"""Offline tests for the classification pipeline (no Cloudflare account needed).

Runs a tiny local HTTP server imitating the Clef API documented in CLEF_API.md,
including success responses, rate limiting (HTTP 429), failure payloads and
malformed answers. Covers:

  - credential parsing
  - request authentication/URL format
  - response validation (invalid label, missing answer, success=false)
  - retries on 429
  - resume behaviour (failed items retried, good ones kept)
  - HTML escaping of comment text in generated pages
  - MISTAKES.md compilation from review exports (complete vs partial review)

Run:  python run_tests.py
"""

import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import classify_comments as cc  # noqa: E402
import compile_mistakes as cm   # noqa: E402
import generate_html as gh      # noqa: E402


def good_response(label="acceptable", confidence=0.42):
    return {
        "result": {
            "model": "clef-flash",
            "answers": {
                "classification": {
                    "type": "choice",
                    "choice": label,
                    "probabilities": {l: 0.333 for l in cc.LABELS},
                    "confidence": confidence,
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 0},
        },
        "success": True, "errors": [], "messages": [],
    }


SAMPLE_COMMENTS = [
    {"id": 1, "author": "A", "thread": "Doubts", "posted_at": "t",
     "comment": "<script>alert('xss')</script> Is the quiz open book?"},
    {"id": 2, "author": "B", "thread": "Doubts", "posted_at": "t",
     "comment": "Join my telegram for solved assignments Rs99!!"},
    {"id": 3, "author": "C", "thread": "Doubts", "posted_at": "t",
     "comment": "Sometimes I wonder if this course is for me at all."},
]


class TempRepo:
    """A temp directory laid out like a mini repo (created/cleaned automatically)."""

    def __enter__(self):
        self.path = Path(tempfile.mkdtemp(prefix="clef-tests-"))
        (self.path / "comments.json").write_text(
            json.dumps({"comments": SAMPLE_COMMENTS}), encoding="utf-8")
        (self.path / "cloud-flare.local.sh").write_text(
            'export CLOUDFLARE_ACCOUNT_ID="test-account"\n'
            'export CLOUDFLARE_API_TOKEN="test-token"\n', encoding="utf-8")
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)


class FakeClefServer:
    """Local API double: replies with the next scripted step, one per request.

    Steps: {"status": <http code>} | {"payload": <json dict>}
    """

    def __init__(self, script):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                self.server.requests.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization", ""),
                    "body": json.loads(body or b"{}"),
                })
                step = (self.server.script.pop(0) if self.server.script
                        else {"payload": good_response()})
                if "status" in step:
                    self.send_response(step["status"])
                    self.end_headers()
                    self.wfile.write(b"simulated HTTP error")
                    return
                payload = json.dumps(step["payload"]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.script = list(script)
        self.requests = []
        self.httpd.requests = self.requests
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def replace_script(self, script):
        self.httpd.script = list(script)

    @property
    def n_requests(self):
        return len(self.requests)


def make_args(temp, server, **overrides):
    """Build an argparse.Namespace the way main() would for dataset 1."""
    kwargs = dict(
        dataset=1, input=str(temp / "comments.json"),
        output=str(temp / "results.json"), model="clef-flash", rubric=None,
        api_base=server.base_url, timeout=10.0, max_retries=1, sleep=0.0,
        test=False,
    )
    kwargs.update(overrides)
    import argparse
    return argparse.Namespace(**kwargs)


class CredentialTests(unittest.TestCase):
    def test_parses_credentials(self):
        with TempRepo() as temp:
            account, token = cc.load_credentials(temp / "cloud-flare.local.sh")
            self.assertEqual(account, "test-account")
            self.assertEqual(token, "test-token")

    def test_missing_credentials_raise(self):
        with TempRepo() as temp:
            (temp / "cloud-flare.local.sh").unlink()
            with self.assertRaises(SystemExit):
                cc.load_credentials(temp / "cloud-flare.local.sh")


class ClassificationTests(unittest.TestCase):
    def run_pipeline(self, temp, server, **overrides):
        old_repo = cc.REPO
        cc.REPO = temp  # so load_credentials and default paths land in temp
        try:
            cc.run(make_args(temp, server, **overrides))
        finally:
            cc.REPO = old_repo
        return json.loads((temp / "results.json").read_text(encoding="utf-8"))

    def test_happy_path_and_request_format(self):
        with TempRepo() as temp:
            server = FakeClefServer([{"payload": good_response("acceptable")},
                                     {"payload": good_response("spam")},
                                     {"payload": good_response("toxic")}])
            try:
                doc = self.run_pipeline(temp, server)
                self.assertEqual([r["label"] for r in doc["results"]],
                                 ["acceptable", "spam", "toxic"])
                self.assertEqual(doc["counts"], {"total": 3, "ok": 3, "failed": 0})
                self.assertEqual(server.n_requests, 3)
                request = server.requests[0]
                self.assertIn("/accounts/test-account/", request["path"])
                self.assertIn("/ai/run/@cf/cloudflare/clef-flash", request["path"])
                self.assertEqual(request["auth"], "Bearer test-token")
                self.assertEqual(request["body"]["state"]["comment"],
                                 SAMPLE_COMMENTS[0]["comment"])
                question = request["body"]["questions"]["classification"]
                self.assertEqual(sorted(question["criteria"]), sorted(cc.LABELS))
            finally:
                server.stop()

    def test_rate_limit_warning_and_success(self):
        script = [{"status": 429}, {"payload": good_response("acceptable")},
                  {"payload": good_response("spam")}, {"payload": good_response("toxic")}]
        with TempRepo() as temp:
            server = FakeClefServer(script)
            try:
                doc = self.run_pipeline(temp, server)
                self.assertEqual(doc["counts"]["ok"], 3)
                self.assertGreaterEqual(server.n_requests, 4)
            finally:
                server.stop()

    def test_malformed_and_failed_payloads_recorde_errors(self):
        steps = [
            {"payload": good_response("obviously-not-a-label")},  # invalid label
            {"payload": {"success": False, "errors": ["boom"], "result": None}},
            {"payload": {"success": True, "result": {"answers": {}}}},  # no answer
        ]
        with TempRepo() as temp:
            server = FakeClefServer(steps)
            try:
                doc = self.run_pipeline(temp, server)
                self.assertEqual(doc["counts"], {"total": 3, "ok": 0, "failed": 3})
                self.assertIn("unexpected label", doc["results"][0]["error"])
                self.assertIn("api reported failure", doc["results"][1]["error"])
                self.assertIn("no answer", doc["results"][2]["error"])
                self.assertIsNone(doc["results"][0]["label"])
            finally:
                server.stop()

    def test_resume_retries_only_failures(self):
        with TempRepo() as temp:
            server = FakeClefServer([{"payload": good_response("acceptable")},
                                     {"status": 500}, {"status": 500}])
            try:
                doc = self.run_pipeline(temp, server)
                self.assertEqual(doc["counts"], {"total": 3, "ok": 1, "failed": 2})
                first_run_requests = server.n_requests  # 1 ok + 2 failed fast
                server.replace_script([{"payload": good_response("spam")},
                                       {"payload": good_response("spam")}])
                doc2 = self.run_pipeline(temp, server)
                remaining_new_requests = server.n_requests - first_run_requests
                self.assertEqual(remaining_new_requests, 2)
                self.assertEqual(doc2["counts"], {"total": 3, "ok": 3, "failed": 0})
                self.assertEqual([r["label"] for r in doc2["results"]],
                                 ["acceptable", "spam", "spam"])
            finally:
                server.stop()


class HTMLTests(unittest.TestCase):
    def test_json_script_escapes_comment_text(self):
        blob = {"comments": SAMPLE_COMMENTS[:1]}
        injected = gh.json_script(blob)
        self.assertNotIn("<script>alert", injected)
        self.assertIn("\\u003cscript>alert", injected)

    def test_generated_report_and_review_pages(self):
        with TempRepo() as fake:
            results_file = fake / "results_clef_flash.json"
            results_file.write_text(json.dumps({
                "dataset": "comments.json", "model": "clef-flash", "rubric": "v1",
                "completed_at": "now-ish",
                "counts": {"total": 3, "ok": 2, "failed": 1},
                "results": [
                    {"id": 1, "model": "clef-flash", "label": "acceptable",
                     "confidence": 0.9, "probabilities": {"acceptable": 1.0}, "error": None},
                    {"id": 2, "model": "clef-flash", "label": "spam",
                     "confidence": 0.8, "probabilities": {"spam": 1.0}, "error": None},
                    {"id": 3, "model": "clef-flash", "label": None,
                     "confidence": None, "probabilities": None, "error": "boom"},
                ]}), encoding="utf-8")
            old_repo = gh.REPO
            gh.REPO = fake
            try:
                gh.build_pages(1)
            finally:
                gh.REPO = old_repo
            report = (fake / "results.html").read_text(encoding="utf-8")
            review = (fake / "review.html").read_text(encoding="utf-8")
            self.assertIn("\\u003cscript>alert", report)
            self.assertNotIn("<script>alert", report)
            self.assertIn('"error": "boom"', report)  # failure surfaced, not hidden
            self.assertIn("succeeded", report.lower())
            self.assertIn("clef-review-v1", review)  # review page wired to store key
            self.assertIn("comment.comment", review)  # renders per-comment


class CompileMistakesTests(unittest.TestCase):
    DECISIONS = {"results": [
        {"id": 1, "choice": "toxic", "borderline": True,
         "note": "personal attack, not criticism of the work"},
        {"id": 2, "choice": "spam", "borderline": False,
         "note": "plain advertising, Clef agrees"},
        {"id": 3, "choice": "acceptable", "borderline": True,
         "note": "self-doubt, not abuse"},
    ]}

    RESULTS = {"dataset": "comments.json", "model": "clef-flash", "rubric": "v1",
               "results": [
                   {"id": 1, "label": "acceptable"},
                   {"id": 2, "label": "spam"},
                   {"id": 3, "label": "toxic"},
               ]}

    def write_inputs(self, temp, decisions_doc):
        (temp / "results_clef_flash.json").write_text(json.dumps(self.RESULTS),
                                                      encoding="utf-8")
        (temp / "clef-review-v1.json").write_text(json.dumps(decisions_doc),
                                                  encoding="utf-8")

    def test_full_review_produces_expected_mistakes_file(self):
        with TempRepo() as temp:
            self.write_inputs(temp, self.DECISIONS)
            old_repo = cm.REPO
            cm.REPO = temp
            try:
                complete = cm.build(1)
            finally:
                cm.REPO = old_repo
            self.assertTrue(complete)
            out = (temp / "MISTAKES.md").read_text(encoding="utf-8")
            self.assertIn("Comment 3", out)      # 3 == clef toxic, mine acceptable
            self.assertIn("My label: **acceptable**", out)
            self.assertIn("Comment 1", out)      # 1 == clef acceptable, mine toxic
            self.assertIn("Borderline calls", out)
            self.assertNotIn("### Comment 2", out)  # agreement, not borderline -> omitted

    def test_partial_review_is_flagged_incomplete(self):
        with TempRepo() as temp:
            self.write_inputs(temp, {"results": self.DECISIONS["results"][:1]})
            old_repo = cm.REPO
            cm.REPO = temp
            try:
                complete = cm.build(1)
            finally:
                cm.REPO = old_repo
            self.assertFalse(complete)  # so an unclear review cannot hide itself
            out = (temp / "MISTAKES.md").read_text(encoding="utf-8")
            self.assertIn("1/3", out)


if __name__ == "__main__":
    unittest.main()
