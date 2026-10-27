#!/usr/bin/env python3
"""Classify course-forum comments using Cloudflare's Clef decision models.

Datasets (choose with --dataset):
  1 -> comments.json    with model clef-flash, results saved to results_clef_flash.json
  2 -> comments-v2.json with model clef,      results saved to results_clef.json
Each of these can be overridden with --input / --model / --output.

Credentials are read from cloud-flare.local.sh (never printed, never stored).
Results are saved after every comment, so interrupted or failed runs can be
resumed with the same command: comments that already succeeded are skipped.

Usage (Windows PowerShell):
  python classify_comments.py --test        # one sample request, prints response shape
  python classify_comments.py --dataset 1   # run or resume dataset 1
  python classify_comments.py --dataset 2   # run or resume dataset 2
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LABELS = ["acceptable", "toxic", "spam"]
CRED_FILE = "cloud-flare.local.sh"

REPO = Path(__file__).resolve().parent

# Two rubrics: v1 is used for dataset 1 (clef-flash), v2 is a refined version
# used for dataset 2 (clef). See NOTES.md for what changed between them.
RUBRICS = {
    "v1": {
        "instructions": (
            "Classify `comment`, a post in the public forum of CS307 Applied AI Lab, "
            "an Indian college course where students build AI artifacts, answer doubts, "
            "and share their work. Read students, a TA and professors. "
            "Pick exactly one label for the post's main purpose and tone."
        ),
        "criteria": {
            "acceptable": (
                "A normal, reasonable contribution to the course forum: genuine questions "
                "and answers, tips, feedback on a classmate's work (even pointed or negative "
                "feedback about the work), thanks, polite disagreement, criticism of the "
                "course, grading or policy, authentic student experiences, harmless jokes "
                "and self-deprecating humour, or Hinglish remarks that are not abusive. "
                "Disagreement and frustration are acceptable unless they turn into insults."
            ),
            "toxic": (
                "Insults, name-calling, mockery directed at a person or group, personal "
                "attacks, threats, intimidation, harassment, or hate/discrimination "
                "targeting people by gender, region, caste, religion, branch of study or "
                "socioeconomic status. Criticism that attacks the person instead of the "
                "work belongs here."
            ),
            "spam": (
                "Unsolicited advertising, promotion or scams: assignment-help or project "
                "services, paid notes, exam-paper leaks for sale, crypto or trading "
                "signals, get-rich-quick or prize scams that ask for payment or personal "
                "data, follower/follower-count services, or any external promotional link "
                "or contact offer whose main purpose is selling rather than course "
                "discussion. A post is not spam merely because it contains a link; a "
                "link that is genuinely useful for the course is acceptable."
            ),
        },
    },
    "v2": {
        "instructions": (
            "Classify `comment`, a post in the public forum of CS307 Applied AI Lab, "
            "an Indian college course where students test and evaluate a chatbot, ask "
            "doubts, and share their work. Read students, a TA and professors. "
            "Pick exactly one label for the post's main purpose and tone."
        ),
        "criteria": {
            "acceptable": (
                "A normal, reasonable contribution to the course forum: genuine questions "
                "and answers, tips, detailed findings from the assignment, feedback on a "
                "classmate's work (even blunt or negative feedback about the work), thanks, "
                "polite disagreement, complaints about grading or policy, reflections on "
                "marks and fairness, harmless jokes and self-deprecating humour, and "
                "Hinglish or regional-language remarks that are not abusive. Blunt "
                "criticism of a report or rubric is acceptable when it attacks the work "
                "and suggests concrete improvements, not the person."
            ),
            "toxic": (
                "Insults ('idiot', 'dumb', 'useless', 'nobody', 'chamcha/suck-up'), "
                "personal mockery (appearance, abilities, personality, 'attention seeker'), "
                "threats or intimidation ('I'll find out who you are... watch your back', "
                "'I'll see you outside the lab'), harassment of a named or identifiable "
                "person, and hate or stereotyping by region, gender, caste or admission "
                "route ('management quota' jabs, jokes about a region's people). "
                "Comments that look like useful tips or feedback but contain a personal "
                "attack belong here."
            ),
            "spam": (
                "Unsolicited advertising, promotion or scams: assignment or report-writing "
                "services, exam-paper leaks for sale, paid notes and viva-question PDFs, "
                "crypto/trading or follower services, jobs or contests that ask for "
                "registration fees or personal data such as Aadhaar numbers, referral or "
                "affiliate links, and 'helpful' posts whose real purpose is a sales pitch "
                "(a genuine tip ending in a discount code or paid-service link is still "
                "spam). Splitting a paid subscription among a few coursemates at a price "
                "is also spam. Phishing pages that impersonate the college belong here. "
                "A post is not spam merely because it contains a link; a genuinely useful "
                "course-related link (a shared script on GitHub, the college's own form) "
                "is acceptable. A student's small in-course survey asking classmates for "
                "responses is borderline: mark it spam only if it clearly solicits for "
                "orbiting personal benefit rather than the assignment itself."
            ),
        },
    },
}

# Dataset presets.
DATASETS = {
    "1": {"input": "comments.json", "model": "clef-flash",
          "output": "results_clef_flash.json", "rubric": "v1",
          "review_page": "review.html", "review_key": "clef-review-v1",
          "html": "results.html"},
    "2": {"input": "comments-v2.json", "model": "clef",
          "output": "results_clef.json", "rubric": "v2",
          "review_page": "review-v2.html", "review_key": "clef-review-v2",
          "html": "results-v2.html"},
}

QUESTION_NAME = "classification"


def load_credentials(cred_path: Path):
    """Parse account ID and token from cloud-flare.local.sh. Values are never printed."""
    if not cred_path.exists():
        raise SystemExit(
            f"ERROR: {cred_path.name} not found. Copy cloud-flare.sh to {cred_path.name}, "
            "fill in your account ID and API token, then re-run. See NOTES.md."
        )
    text = cred_path.read_text(encoding="utf-8")
    creds = dict(re.findall(r'export\s+(\w+)="?([^"\n]*)"?\s*', text))
    if "CLOUDFLARE_ACCOUNT_ID" not in creds or "CLOUDFLARE_API_TOKEN" not in creds:
        raise SystemExit(
            f"ERROR: {cred_path.name} must define CLOUDFLARE_ACCOUNT_ID and "
            f"CLOUDFLARE_API_TOKEN (copy cloud-flare.sh to {cred_path.name} and fill in "
            "your values). See NOTES.md."
        )
    return creds["CLOUDFLARE_ACCOUNT_ID"], creds["CLOUDFLARE_API_TOKEN"]


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def classify_once(account_id: str, token: str, model: str, rubric: dict,
                  comment_text: str, api_base: str, timeout: float) -> dict:
    """One API call. Returns {'label','confidence','probabilities'} or raises."""
    url = (f"{api_base}/accounts/{account_id}/ai/run/@cf/cloudflare/{model}")
    payload = {
        "state": {"comment": comment_text},
        "questions": {
            QUESTION_NAME: {
                "type": "choice",
                "instructions": rubric["instructions"],
                "criteria": rubric["criteria"],
            }
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=auth_header(token),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)

    # Validate the response the API documents (see CLEF_API.md).
    if not data.get("success", False):
        raise APIError("api reported failure: " + json.dumps(data.get("errors", [])))
    answers = (data.get("result") or {}).get("answers") or {}
    answer = answers.get(QUESTION_NAME)
    if not answer or "choice" not in answer:
        raise APIError("response had no answer for the classification question")
    label = answer["choice"]
    if label not in LABELS:
        raise APIError(f"unexpected label {label!r}; expected one of {LABELS}")
    return {
        "label": label,
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities"),
    }


class APIError(Exception):
    """A request that failed or returned an unusable answer (no secrets inside)."""


def classify_comment(account_id, token, model, rubric, comment_text,
                     api_base, timeout, max_retries):
    """With retries: 429 (rate limit) waits and retries; other errors fail fast
    so the run can be resumed later without burning more quota."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return classify_once(account_id, token, model, rubric, comment_text,
                                 api_base, timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(2048).decode("utf-8", "replace")
            except Exception:
                pass
            if exc.code == 429 and attempt <= max_retries:
                wait = min(60, 5 * attempt)
                print(f"  [rate-limited] waiting {wait}s before retry "
                      f"(attempt {attempt}/{max_retries})")
                time.sleep(wait)
                continue
            raise APIError(f"HTTP {exc.code}: {detail or 'request failed'}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt <= max_retries:
                time.sleep(min(30, 3 * attempt))
                continue
            raise APIError(f"network problem: {exc.reason}") from None


def run(args):
    preset = DATASETS[str(args.dataset)]
    input_path = REPO / (args.input or preset["input"])
    output_path = REPO / (args.output or preset["output"])
    model = args.model or preset["model"]
    rubric = RUBRICS[args.rubric or preset["rubric"]]

    dataset = json.loads(input_path.read_text(encoding="utf-8"))
    comments = dataset.get("comments", [])
    if not comments or not all("id" in c and "comment" in c for c in comments):
        raise SystemExit(f"ERROR: {input_path.name} is not in the expected format "
                         "(a dict with a 'comments' list of {id, comment, ...}).")
    print(f"Dataset: {input_path.name} | comments: {len(comments)} | "
          f"model: {model} | output: {output_path.name}")

    # Load previous results so a re-run only processes missing/failed items.
    all_results = []
    saved = {}
    if output_path.exists():
        try:
            saved = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"WARNING: could not parse existing {output_path.name}; "
                  "starting a fresh results file")
    done = {r["id"]: r for r in saved.get("results", [])
            if r.get("label") and not r.get("error")}
    print(f"Already classified successfully: {len(done)} "
          f"({len(comments) - len(done)} to (re)process)")

    account_id, token = load_credentials(REPO / CRED_FILE)

    for index, comment in enumerate(comments, start=1):
        cid, text = comment["id"], comment["comment"]
        if cid in done:
            all_results.append(done[cid])
            continue
        try:
            outcome = classify_comment(account_id, token, model, rubric, text,
                                       args.api_base, args.timeout, args.max_retries)
            outcome.update(id=cid, model=model, error=None,
                           classified_at=datetime.now(timezone.utc).isoformat())
            all_results.append(outcome)
            print(f"  [{index}/{len(comments)}] id={cid} -> {outcome['label']} "
                  f"(confidence={outcome['confidence']})")
        except APIError as exc:
            outcome = {"id": cid, "model": model, "label": None, "error": str(exc),
                       "classified_at": datetime.now(timezone.utc).isoformat()}
            all_results.append(outcome)
            print(f"  [{index}/{len(comments)}] id={cid} -> ERROR: {exc}")
        time.sleep(args.sleep)

    ok = sum(1 for r in all_results if r.get("label") and not r.get("error"))
    failed = len(all_results) - ok
    output = {
        "dataset": input_path.name, "model": model,
        "rubric": args.rubric or preset["rubric"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "counts": {"total": len(comments), "ok": ok, "failed": failed},
        "results": all_results,
    }
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"Saved {output_path.name}: ok={ok}, failed={failed}. "
          "Re-run the same command to retry only failures.")


def test_call(args):
    """Send one sample comment, print the raw response structure (no secrets)."""
    preset = DATASETS[str(args.dataset)]
    model = args.model or preset["model"]
    rubric = RUBRICS[args.rubric or preset["rubric"]]
    account_id, token = load_credentials(REPO / CRED_FILE)
    sample = "Submitted my artifact today. The CSV parsing took longer than the UI!"
    try:
        data = classify_once(account_id, token, model, rubric, sample,
                             args.api_base, args.timeout)
        print("SUCCESS. Parsed answer for one sample comment:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except APIError as exc:
        print(f"TEST FAILED: {exc}")
        print("Check your account ID / token in cloud-flare.local.sh "
              "(401/403 = wrong credentials, 429 = wait and retry).")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=int, choices=(1, 2), required=True,
                        help="1 = comments.json (clef-flash), 2 = comments-v2.json (clef)")
    parser.add_argument("--input", help="override the dataset's input JSON file")
    parser.add_argument("--model", choices=("clef", "clef-flash"),
                        help="override the model for this dataset")
    parser.add_argument("--output", help="override the results JSON file")
    parser.add_argument("--rubric", choices=tuple(RUBRICS),
                        help="override the rubric wording for this run")
    parser.add_argument("--test", action="store_true",
                        help="send one sample request and print the response")
    parser.add_argument("--sleep", type=float, default=0.25,
                        help="seconds to wait between API calls (default 0.25)")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="per-request timeout in seconds (default 60)")
    parser.add_argument("--max-retries", type=int, default=5,
                        help="max retries per comment (default 5)")
    parser.add_argument("--api-base",
                        default="https://api.cloudflare.com/client/v4",
                        help="API base URL (overridable for offline tests only)")
    args = parser.parse_args()
    if args.test:
        test_call(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
