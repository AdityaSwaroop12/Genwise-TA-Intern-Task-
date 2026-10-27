Placeholders pending review.

Both datasets still need my manual review (the review must be my own judgement,
not AI-generated). Until then, these files intentionally contain no claimed
mistakes.

Status:
- [ ] Dataset 1 (`comments.json`, clef-flash) reviewed and compiled into this file
- [ ] Dataset 2 (`comments-v2.json`, clef) reviewed and compiled into MISTAKES-v2.md

How this gets filled in (see NOTES.md):
1. Run the classifier, then open `review.html` / `review-v2.html`.
2. For every comment, pick the label I believe is correct (acceptable / toxic /
   spam), tick "borderline" where it applies, and write a short reason whenever
   I disagree with Clef.
3. Click "Export decisions as JSON" (saves `clef-review-v1.json` /
   `clef-review-v2.json`).
4. Run `python compile_mistakes.py --dataset 1` and
   `python compile_mistakes.py --dataset 2` — they generate/replace this file
   and `MISTAKES-v2.md` from MY exported decisions.
