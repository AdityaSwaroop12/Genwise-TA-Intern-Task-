# NOTES.md — how this assignment was implemented and how to run it

## 1. What is here

| File | Purpose |
| --- | --- |
| `classify_comments.py` | Classifies both datasets with Cloudflare's Clef models (stdlib only, no dependencies). Saves machine-readable results and supports resume. |
| `generate_html.py` | Builds `results.html` / `results-v2.html` reports and `review.html` / `review-v2.html` review pages **from the saved results** (no API calls). |
| `compile_mistakes.py` | Builds `MISTAKES.md` / `MISTAKES-v2.md` from my exported review decisions. |
| `run_tests.py` | Offline test suite with a fake local Clef API — no Cloudflare account needed. |
| `results_clef_flash.json` / `results_clef.json` | Saved classifications (written by the classifier after a real run). |
| `results.html` / `results-v2.html` | Reports to submit. |
| `review.html` / `review-v2.html` | Manual-review pages for me. |
| `clef-review-v1.json` / `clef-review-v2.json` | My exported review decisions (backup of what I clicked). |
| `MISTAKES.md` / `MISTAKES-v2.md` | The mistakes I found by reading every comment myself. |
| `cloud-flare.local.sh` | My Cloudflare credentials. **Never committed** (in `.gitignore`). |

## 2. Configure Cloudflare credentials (Windows PowerShell)

1. Create a free Cloudflare account and add any domain/workers setup as instructed in the
   dashboard (Workers AI works on the free tier).
2. Create an API token: dash.cloudflare.com → My Profile → **API Tokens** → *Create Token*.
   Give it the **Workers AI - Read** and **Workers AI - Edit** permissions.
3. Copy your **Account ID** from any account page sidebar.
4. In this repo folder, run (the token will not be displayed anywhere):

```powershell
Copy-Item cloud-flare.sh cloud-flare.local.sh
notepad cloud-flare.local.sh
```

5. Replace the two placeholder values in `cloud-flare.local.sh` (keep the format
   `export CLOUDFLARE_ACCOUNT_ID="..."` and `export CLOUDFLARE_API_TOKEN="..."`).
   Save and close. The file stays git-ignored.

Verify it works:

```powershell
python classify_comments.py --test --dataset 1
```

(No secrets are printed — only the parsed answer for one sample comment.)
The classifier parses `cloud-flare.local.sh` itself; there is no need for a real
Bash shell on Windows.

## 3. Run the classifier

```powershell
python classify_comments.py --dataset 1   # comments.json  + model clef-flash  -> results_clef_flash.json
python classify_comments.py --dataset 2   # comments-v2.json + model clef -> results_clef.json
```

- Results are saved after every comment. Re-running the same command **skips
  comments that already succeeded and retries only failures** — safe to re-run.
- 100 comments each; ~1 API call per comment, 0.25 s apart, with retries on
  HTTP 429/timeout (up to 5 attempts per comment, back-off capped at 60 s).
- Any comment that still fails keeps its `"error"` in the results file; it is
  never silently skipped or given a made-up label. The report shows it as an
  API error until a successful re-run fixes it.
- Useful flags: `--model`, `--input`, `--output`, `--rubric`, `--sleep`,
  `--timeout`, `--max-retries` (all documented in `python classify_comments.py --help`).
- For testing there is also `--api-base` (points at a local fake API; used by run_tests.py, not for real runs).

## 4. Regenerate both HTML reports (no API needed)

```powershell
python generate_html.py --dataset 1   # -> results.html  + review.html
python generate_html.py --dataset 2   # -> results-v2.html + review-v2.html
```

Reports embed the saved results as JSON and render cards per comment with:
Clef's label (colour-coded), the model name, the comment's full original text
(author, thread, time, id), confidence + per-option probabilities, and any API
error for the failed ones. There is a search box (text / author / id), a label
filter (all / acceptable / toxic / spam / error) and per-category counts.

Escaping: comment text is embedded with `<` escaped (`\u003c`) inside the JSON
blob, and every render goes through `textContent`/`createTextNode`, so a
comment containing `<script>` is displayed as inert text; nothing supplied by a
comment is ever executed.

## 5. Complete the manual review (my judgement — not AI's)

1. Open `review.html` (dataset 1) or `review-v2.html` (dataset 2) in a browser.
   Each page shows every comment with Clef's predicted label.
2. For each comment, pick **my** label (acceptable / toxic / spam). Tick
   `borderline` where it applies. Whenever I disagree with Clef (or keep its
   label on a borderline call), write 1–2 sentences in the note box.
3. Everything saves automatically in the browser (localStorage). Progress shows
   `N of 100 reviewed`; comments with no label selected are **not** counted as
   reviewed. Reopen any time to resume; nothing is lost.
4. Click **Export decisions as JSON** → saves `clef-review-v1.json` /
   `clef-review-v2.json` in Downloads (move them into the repo folder).
   **Import decisions** restores them (e.g. after changing browser or machine).
5. Compile the mistakes files from *my* decisions:

```powershell
python compile_mistakes.py --dataset 1   # -> MISTAKES.md
python compile_mistakes.py --dataset 2   # -> MISTAKES-v2.md
```

`compile_mistakes.py` refuses to produce a "complete" file if any comment is
still unreviewed, and lists every mistake with: comment id, Clef's label, my
label, one or two sentences of explanation, plus a separate section for
borderline calls.

## 6. Tests

```powershell
python run_tests.py
```

10 offline tests (fake local Clef API, no account needed): credential parsing,
request URL/auth format, label validation, retry on 429, error recording for
malformed payloads, resume-only-failures, HTML escaping, report generation, and
MISTAKES compilation (including rejection of incomplete review). Python 3.13,
standard library only — no `pip install` required.
**Not yet runnable without credentials:** the real `--test`/`--dataset` runs
(they need valid Cloudflare credentials) and browser-side checks of the review
pages (only syntax-tested so far).

## 7. Tools and models actually used

- **Coding agent:** opencode (GLM — a code-writing LLM) wrote the Python and
  HTML programs, the tests and this file, and ran the offline test suite.
- **Classifiers:** Cloudflare Workers AI Clef — `clef-flash` for dataset 1,
  `clef` for dataset 2 — via the exact request/response format in `CLEF_API.md`.
- **Human review:** the intern (me), covering 100 + 100 comments, recorded in
  the review pages and compiled into the two MISTAKES files.
- The rubric wording (`RUBRICS` dict in `classify_comments.py`) is mine to
  tune; AI helped draft it. It stays initial guidance, not a substitute for the
  manual review.

## 8. Rubric changes between dataset 1 and dataset 2

Dataset 2 uses a refined rubric (documented here because the split duplicate
labels were improved from what dataset 1 exposed):
- explicit examples of insult vocabulary and threats seen in the data
  ('idiot', 'useless', threats containing 'watch your back');
- "looks like a tip but ends in a sales pitch ⇒ spam" (the dataset mixes
  genuine tips with ads in the same comment);
- subscription-reselling among coursemates counts as spam;
- phishing pages impersonating college systems count as spam (dataset 1
  treated many scams generically);
- blunt-but-constructive criticism of a report/rubric is explicitly acceptable;
- regional-language (Hinglish) insults are explicitly in scope for `toxic`.

## 9. Reflection (draft — to be personalised before submitting)

*(This section is a draft for me to rewrite in my own words after finishing the
review; the tools sentence above stays factual.)*

TODO: how it felt using AI to build the classifier; what surprised me in the
differences between clef-flash and clef; how many of Clef's predictions I agree
with and the categories where it struggled (compare after compiling the
mistakes files); what I'd change about the rubric next.

## 10. Known limitations

- Label probabilities differ slightly when several questions share a request
  (per CLEF_API.md); we ask exactly one question per request for consistency.
- A wrong-but-confident Clef answer still gets recorded; confidence is shown in
  the reports so the human review can prioritise low-confidence rows.
- The review page stores decisions in localStorage per browser profile;
  export/import JSON is the durable backup path.
- The classifier is sequential (never parallel) to stay polite to the free-tier
  rate limit.
