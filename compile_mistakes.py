#!/usr/bin/env python3
"""Build MISTAKES.md / MISTAKES-v2.md from the human review export files.

This script does not judge comments itself. It only compiles decisions that
were made in the review pages (review.html / review-v2.html) and exported via
the "Export decisions as JSON" button (default export names:
clef-review-v1.json, clef-review-v2.json).

Usage:
  python compile_mistakes.py --dataset 1   # reads clef-review-v1.json  -> MISTAKES.md
  python compile_mistakes.py --dataset 2   # reads clef-review-v2.json  -> MISTAKES-v2.md

Exits with an error if some comments have not been reviewed yet, so an
incomplete review cannot quietly end up in the final markdown.
"""

import argparse
import json
import sys
from pathlib import Path

from classify_comments import DATASETS, LABELS

REPO = Path(__file__).resolve().parent


def compiled_marks(mistakes: list, borderline: list, reviewed: int, total: int) -> str:
    lines = []
    lines.append(f"- Review complete: {reviewed}/{total} comments manually reviewed.\n")
    lines.append(f"- Mistakes found: {len(mistakes)}.\n")
    lines.append(f"- Borderline calls: {len(borderline)} (included where marked).\n")
    return "".join(lines)


def build(dataset: int) -> bool:
    preset = DATASETS[str(dataset)]
    decisions_path = REPO / (preset["review_key"] + ".json")
    results_path = REPO / preset["output"]
    output_path = REPO / ("MISTAKES.md" if dataset == 1 else "MISTAKES-v2.md")

    results_doc = json.loads(results_path.read_text(encoding="utf-8"))
    clef_labels = {r["id"]: r for r in results_doc["results"]}

    if not decisions_path.exists():
        print(f"ERROR: {decisions_path.name} not found. Open the review page "
              f"({preset['review_page']}), review the comments, and click "
              "'Export decisions as JSON' first. See NOTES.md.")
        return False

    doc = json.loads(decisions_path.read_text(encoding="utf-8"))
    rows = doc.get("results")
    if rows is None and isinstance(doc, list):
        rows = doc
    if not isinstance(rows, list):
        print(f"ERROR: {decisions_path.name} has no 'results' list. "
              "Export it from the review page.")
        return False

    total = len(results_doc["results"])
    decisions = {}
    for row in rows:
        cid = row.get("id")
        choice = row.get("choice")
        if not isinstance(cid, int) or cid not in clef_labels:
            print(f"WARNING: ignoring decision for unknown comment id {cid!r}")
            continue
        if choice not in LABELS:
            print(f"WARNING: comment {cid} has no valid choice; not counted as reviewed")
            continue
        decisions[cid] = row

    reviewed = len(decisions) if len(decisions) <= total else total
    mistakes, borderline = [], []
    for cid in range(1, total + 1):
        row = decisions.get(cid)
        if not row:
            continue
        clef_label = (clef_labels[cid].get("label") or "").lower()
        my_label = row["choice"]
        note = (row.get("note") or "").strip()
        entry = f"### Comment {cid}\n\n- Clef's label: **{clef_label or '(API error)'}**\n- My label: **{my_label}**\n- Why: {note or '(no explanation given)'}\n"
        if clef_label != my_label:
            mistakes.append(entry)
        elif row.get("borderline"):
            borderline.append(entry)

    complete = len(decisions) == total
    header = []
    header.append(f"# Mistakes found in Clef ({results_doc.get('model')}) on {results_doc.get('dataset')}\n\n")
    header.append(compiled_marks(mistakes, borderline, len(decisions), total))
    if mistakes:
        header.append("\n## Wrong classifications\n\n")
        header.extend(mistakes)
    else:
        header.append("\n## Wrong classifications\n\nNone recorded by the reviewer.\n")
    if borderline:
        header.append("\n## Borderline calls (Clef's label kept, but flagged)\n\n")
        header.extend(borderline)
    output_path.write_text("".join(header), encoding="utf-8")
    print(f"wrote {output_path.name}: {len(mistakes)} mistake(s), "
          f"{len(borderline)} borderline call(s), {len(decisions)}/{total} reviewed")
    if not complete:
        print(f"WARNING: {total - len(decisions)} comment(s) are still unreviewed - "
              f"open {preset['review_page']} and finish the review, then re-run this.")
    return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    complete = build(args.dataset)
    if not complete:
        sys.exit(1)


if __name__ == "__main__":
    main()
