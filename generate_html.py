#!/usr/bin/env python3
"""Generate the HTML reports and the human-review pages from saved results.

Reads the machine-readable result files written by classify_comments.py
(no API calls are made), builds results.html / results-v2.html reports and
review.html / review-v2.html pages that the intern uses to record manual
judgements. Everything is a single self-contained HTML file with the data
embedded as JSON; it works offline from a file:// path.

Usage (Windows PowerShell):
  python generate_html.py --dataset 1   # -> results.html  + review.html
  python generate_html.py --dataset 2   # -> results-v2.html + review-v2.html
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from classify_comments import DATASETS, LABELS

REPO = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Shared page skeleton. The data blob is embedded in a JSON script tag with
# "<" escaped so "</script>" inside a comment can never break out of it.
# All rendering uses createElement/textContent, so comment text (even things
# that look like HTML) is always shown as text, never executed.
# ---------------------------------------------------------------------------

CSS = """
:root { --ok:#1a7f37; --warn:#b0611a; --bad:#b42318; --spam:#6f3fb0; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", system-ui, sans-serif; margin: 0; background:#f5f6f8; color:#1d1d1f; }
header { background:#24292f; color:#fff; padding: 16px 24px; }
header h1 { margin: 0 0 4px; font-size: 20px; }
header .sub { color:#adbac7; font-size: 13px; }
main { max-width: 1000px; margin: 0 auto; padding: 20px 16px 60px; }
.stats { display:flex; flex-wrap: wrap; gap:10px; margin: 14px 0; }
.stat { background:#fff; border-radius: 8px; padding: 8px 14px; box-shadow: 0 1px 2px rgba(0,0,0,.08); font-size:13px; }
.stat b { font-size: 16px; }
.tools { display:flex; flex-wrap:wrap; gap:8px; margin: 12px 0; }
.tools input, .tools select, .btn { padding: 7px 10px; border:1px solid #c9cdd3; border-radius: 6px; font-size: 13px; background:#fff; }
.btn { cursor:pointer; }
.btn.primary { background:#24292f; color:#fff; border-color:#24292f; }
.card { background:#fff; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,.08); padding: 14px 16px; margin: 12px 0; }
.card .meta { font-size: 12px; color:#57606a; margin-bottom: 6px; }
.card .meta .id { font-weight: 600; color:#1d1d1f; }
.card .text { white-space: pre-wrap; line-height: 1.5; }
.tag { display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; color:#fff; margin-left: 8px; vertical-align: middle; }
.tag.acceptable { background: var(--ok); }
.tag.toxic { background: var(--bad); }
.tag.spam { background: var(--spam); }
.tag.error { background: var(--warn); }
.tag.pending { background: #57606a; }
.tag.flagged { background: var(--bad); outline: 0; }
.card.error .text { color:#893000; }
.card.toxic .meta { border-left: 3px solid var(--bad); }
.card.acceptable .meta { border-left: 3px solid var(--ok); }
.card.spam .meta { border-left: 3px solid var(--spam); }
.prob { font-size: 11px; color:#57606a; margin-top: 8px; }
.review-controls { display:flex; flex-wrap:wrap; gap: 10px; align-items:center; margin-top: 10px; }
.review-controls label.choice { font-size: 13px; cursor:pointer; margin-right: 6px; }
.review-controls input[type=radio] { accent-color:#24292f; }
textarea { width: 100%; min-height: 44px; margin-top: 8px; border:1px solid #c9cdd3; border-radius: 6px; font: inherit; font-size: 13px; padding: 6px; }
.progress { font-size: 13px; background:#fff; box-shadow:0 1px 2px rgba(0,0,0,.08); border-radius:8px; padding:8px 14px; margin:14px 0; }
.progress .bar { height: 8px; background:#e3e6ea; border-radius:99px; margin-top:6px; }
.progress .bar div { height: 100%; background: var(--ok); border-radius:99px; width:0; transition: width .2s; }
.note { font-size:12px; color:#57606a; margin-top:8px; }
.hidden { display:none !important; }
"""


def json_script(blob: dict) -> str:
    text = json.dumps(blob, ensure_ascii=False)
    # Escape "<" so comment text cannot close the script tag or inject markup.
    return "<script id='data' type='application/json'>" + \
        text.replace("<", "\\u003c") + "</script>"


def page(title: str, subtitle: str, body_js: str, blob: dict) -> str:
    return (
        "<!DOCTYPE html>\n<html lang='en'><head><meta charset='utf-8'>\n"
        "<title>" + title + "</title>\n<style>" + CSS + "</style></head>\n"
        "<body><header><h1>" + title + "</h1><div class='sub'>" + subtitle +
        "</div></header>\n<main id='app'></main>\n" + json_script(blob) +
        "\n<script>\n" + body_js + "\n</script>\n</body></html>\n"
    )


METADATA_JS = """
const DATA = JSON.parse(document.getElementById('data').textContent);
const app = document.getElementById('app');
const LABELS = ['acceptable', 'toxic', 'spam'];
const byId = {};
DATA.results.forEach(r => { byId[r.id] = r; });

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    node.appendChild(typeof child === 'object' ? child : document.createTextNode(String(child)));
  }
  return node;
}
const esc = node => el('span', null, node);  // text nodes are escaped by the browser

function labelTag(item) {
  if (item && item.label) {
    return el('span', { class: 'tag ' + item.label }, item.label);
  }
  return el('span', { class: 'tag error' },
    item && item.error ? 'API error' : 'no result');
}

function counts(data) {
  const c = { acceptable: 0, toxic: 0, spam: 0, ok: 0, failed: 0 };
  for (const item of data.results) {
    if (item.label) { c[item.label]++; c.ok++; } else { c.failed++; }
  }
  return c;
}

// Progress bar shared by the review pages.
function progressNode(done, total, barId) {
  const bar = el('div', { class: 'bar' }, el('div', { id: barId }));
  const p = el('div', { class: 'progress' },
    el('span', { id: 'progressText' }, done + ' of ' + total + ' reviewed'),
    bar);
  requestAnimationFrame(() => { const f = document.getElementById(barId); if (f) f.style.width = (done / total * 100) + '%'; });
  return p;
}
function updateProgress(done, total, barId) {
  const t = document.getElementById('progressText');
  const f = document.getElementById(barId);
  if (t) t.textContent = done + ' of ' + total + ' reviewed';
  if (f) f.style.width = (total ? done / total * 100 : 0) + '%';
}
"""

# ---------------------------------------------------------------------------
# Report page renderer (results.html / results-v2.html)
# ---------------------------------------------------------------------------

REPORT_JS = METADATA_JS + """

function commentCard(comment) {
  const item = byId[comment.id];
  const card = el('div', { class: 'card ' + (item && item.label ? item.label : 'error') });
  const parts = ['id: ' + comment.id];
  if (comment.author) parts.push('author: ' + comment.author);
  if (comment.thread) parts.push('thread: ' + comment.thread);
  if (comment.posted_at) parts.push('posted: ' + comment.posted_at);
  const meta = el('div', { class: 'meta' },
    el('span', { class: 'id' }, '#' + comment.id),
    ' ' + parts.slice(1).join(' | ') + '  |  Clef (' + DATA.meta.model + '): ');
  meta.appendChild(labelTag(item));
  card.appendChild(meta);
  const text = el('div', { class: 'text' }); text.textContent = comment.comment;
  card.appendChild(text);
  if (item && item.label && item.probabilities) {
    const probs = Object.entries(item.probabilities).map(([k, v]) => k + ': ' + v).join(' | ');
    card.appendChild(el('div', { class: 'prob' },
      'confidence: ' + item.confidence + '  |  probabilities: ' + probs));
  }
  if (item && item.error) card.appendChild(el('div', { class: 'prob' }, 'API error: ' + item.error));
  return card;
}

const c = counts(DATA);
app.appendChild(el('div', { class: 'stats' },
  el('div', { class: 'stat' }, el('b', null, DATA.results.length), ' comments processed'),
  el('div', { class: 'stat' }, 'acceptable: ', el('b', null, c.acceptable)),
  el('div', { class: 'stat' }, 'toxic: ', el('b', null, c.toxic)),
  el('div', { class: 'stat' }, 'spam: ', el('b', null, c.spam)),
  el('div', { class: 'stat' }, 'succeeded: ', el('b', null, c.ok)),
  el('div', { class: 'stat' }, 'failed: ', el('b', null, c.failed))));

let filter = 'all', query = '';
const list = el('div');

function matches(comment) {
  const item = byId[comment.id] || {};
  if (filter !== 'all' && (item.label || 'error').split(':')[0] !== filter) return false;
  if (filter === 'error' && item.label) return false;
  if (!query) return true;
  const hay = String(comment.id) + ' ' + comment.comment + ' ' + (comment.author || '');
  return hay.toLowerCase().includes(query.toLowerCase());
}
function render() {
  list.replaceChildren(...DATA.comments.filter(matches).map(commentCard));
}

const search = el('input', { placeholder: 'Search text / author / id...', oninput: e => { query = e.target.value; render(); } });
search.style.flex = '1';
const select = el('select', { onchange: e => { filter = e.target.value; render(); } },
  ...['all', 'acceptable', 'toxic', 'spam', 'error'].map(v =>
    el('option', { value: v }, v === 'all' ? 'show all' : v)));

app.appendChild(el('div', { class: 'tools' }, search, select));
app.appendChild(list);
render();
"""

# ---------------------------------------------------------------------------
# Review page renderer (review.html / review-v2.html)
# ---------------------------------------------------------------------------

REVIEW_JS = METADATA_JS + """

const STORE_KEY = DATA.reviewKey + '-decisions';

// decisions: { "<comment id>": { id, choice, borderline, note } }
function loadDecisions() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch { return {}; }
}
function saveDecisions(d) { localStorage.setItem(STORE_KEY, JSON.stringify(d)); }

let decisions = loadDecisions();
const cards = {};

function reviewerControls(comment) {
  const saved = decisions[comment.id] || {};
  const box = el('div', { class: 'review-controls' });
  box.appendChild(el('span', null, 'My label:'));
  const radios = {};
  for (const label of LABELS) {
    const radio = el('input', { type: 'radio', name: 'c' + comment.id, value: label });
    radio.checked = saved.choice === label;
    radios[label] = radio;
    box.appendChild(el('label', { class: 'choice' }, radio, ' ' + label));
  }
  const borderline = el('input', { type: 'checkbox' });
  borderline.checked = !!saved.borderline;
  box.appendChild(el('label', { class: 'choice' }, borderline, ' borderline'));

  const status = el('span', { class: 'tag ' + (saved.choice || 'pending') },
    saved.choice ? 'reviewed: ' + saved.choice : 'pending');

  function persist() {
    let choice = null;
    for (const label of LABELS) if (radios[label].checked) choice = label;
    if (!choice) delete decisions[comment.id];
    else decisions[comment.id] = { id: comment.id, choice, borderline: borderline.checked, note: note.value };
    saveDecisions(decisions);
    status.textContent = choice ? 'reviewed: ' + choice : 'pending';
    status.className = 'tag ' + (choice || 'pending');
    updateProgress(Object.keys(decisions).length, DATA.comments.length, 'reviewBar');
    refreshExportable();
  }
  for (const label of LABELS) radios[label].addEventListener('change', persist);
  borderline.addEventListener('change', persist);

  const note = el('textarea', {
    placeholder: 'Why? (explain mistakes and borderline calls here - required when you disagree with Clef)',
    oninput: persist,
  });
  note.value = saved.note || '';

  return { box, note, status };
}

function reviewCard(comment) {
  const item = byId[comment.id] || {};
  const saved = decisions[comment.id] || {};
  const card = el('div', { class: 'card ' + (saved.choice || 'pending'), id: 'card-' + comment.id });
  card.appendChild(el('div', { class: 'meta' },
    el('span', { class: 'id' }, '#' + comment.id),
    ' ' + [comment.author, comment.thread, comment.posted_at].filter(Boolean).join(' | '),
    '  |  Clef (' + DATA.meta.model + '): '));
  card.children[0].appendChild(labelTag(item));
  if (item.error) card.appendChild(el('div', { class: 'prob' }, 'API error: ' + item.error));
  const text = el('div', { class: 'text' }); text.textContent = comment.comment;
  card.appendChild(text);
  if (item.label && item.probabilities) {
    const probs = Object.entries(item.probabilities).map(([k, v]) => k + ': ' + v).join(' | ');
    card.appendChild(el('div', { class: 'prob' }, 'confidence: ' + item.confidence + ' | probabilities: ' + probs));
  }
  const controls = reviewerControls(comment);
  card.appendChild(controls.box);
  card.appendChild(controls.note);
  if (saved.borderline) card.appendChild(el('div', { class: 'note' }, 'flagged as borderline'));
  cards[comment.id] = card;
  return card;
}

let unreviewedOnly = false;

function render() {
  const nodes = DATA.comments
    .filter(c2 => !unreviewedOnly || !decisions[c2.id])
    .sort((a, b) => Number(a.id) - Number(b.id))
    .map(reviewCard);
  list.replaceChildren(...nodes);
}

const c = counts(DATA);
app.appendChild(el('div', { class: 'stats' },
  el('div', { class: 'stat' }, 'acceptable (Clef): ', el('b', null, c.acceptable)),
  el('div', { class: 'stat' }, 'toxic (Clef): ', el('b', null, c.toxic)),
  el('div', { class: 'stat' }, 'spam (Clef): ', el('b', null, c.spam)),
  el('div', { class: 'stat' }, 'API failures: ', el('b', null, c.failed))));

app.appendChild(progressNode(Object.keys(decisions).length, DATA.comments.length, 'reviewBar'));

const list = el('div');

const toggle = el('input', { type: 'checkbox', onchange: e => {
  unreviewedOnly = e.target.checked; render();
} });
app.appendChild(el('div', { class: 'tools' },
  el('label', { class: 'choice' }, toggle, ' show unreviewed only')));

app.appendChild(list);
render();

function refreshExportable() {
  exportBtn.disabled = Object.keys(decisions).length === 0;
}

const exportBtn = el('button', { class: 'btn primary', onclick: () => {
  const blob = new Blob([JSON.stringify({
    exportedAt: new Date().toISOString(),
    reviewKey: DATA.reviewKey,
    dataset: DATA.meta.dataset,
    results: Object.values(decisions).sort((a, b) => a.id - b.id),
  }, null, 2)], { type: 'application/json' });
  const a = el('a', { href: URL.createObjectURL(blob), download: DATA.reviewKey + '.json' });
  document.body.appendChild(a); a.click(); a.remove();
} }, 'Export decisions as JSON');
refreshExportable();

const importInput = el('input', { type: 'file', accept: 'application/json,.json' });
importInput.addEventListener('change', () => {
  const file = importInput.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const incoming = JSON.parse(reader.result);
      const rows = Array.isArray(incoming) ? incoming : incoming.results;
      if (!rows) throw new Error('no results array');
      let applied = 0;
      for (const row of rows) {
        if (!row || !row.id || !LABELS.includes(row.choice)) continue;
        decisions[row.id] = { id: row.id, choice: row.choice, borderline: !!row.borderline, note: row.note || '' };
        applied++;
      }
      saveDecisions(decisions);
      window.alert('Imported ' + applied + ' decision(s).');
      window.location.reload();
    } catch (err) {
      window.alert('Import failed: ' + err.message);
    }
  };
  reader.readAsText(file);
});
const importBtn = el('button', { class: 'btn', onclick: () => importInput.click() }, 'Import decisions');
app.appendChild(el('div', { class: 'tools' }, exportBtn, importBtn,
  el('button', { class: 'btn', onclick: () => {
    if (window.confirm('Delete all review decisions for this dataset stored in this browser?')) {
      decisions = {}; saveDecisions(decisions); window.location.reload();
    }
  } }, 'Clear saved decisions')));

app.appendChild(el('div', { class: 'note' },
  'Decisions are saved automatically in this browser after every change (localStorage key: ' + DATA.reviewKey +
  '-decisions). Use Export to create a backup JSON file (e.g. review-decisions.json) and Import to restore it or continue on another machine. ' +
  'Comments with no label selected are NOT counted as reviewed. After exporting, run compile_mistakes.py to build MISTAKES.md.'));
"""


def build_pages(dataset: int):
    preset = DATASETS[str(dataset)]
    input_path = REPO / preset["input"]
    results_path = REPO / preset["output"]

    comments = json.loads(input_path.read_text(encoding="utf-8")).get("comments", [])
    results_doc = json.loads(results_path.read_text(encoding="utf-8"))

    covered = {c["id"] for c in comments}
    items = [r for r in results_doc.get("results", []) if r["id"] in covered]
    missing = covered - {r["id"] for r in items}
    if missing:
        print(f"WARNING: {results_path.name} has no entry for comment ids {sorted(missing)}")

    blob = {
        "meta": {"dataset": input_path.name,
                 "model": results_doc.get("model"),
                 "rubric": results_doc.get("rubric"),
                 "completed_at": results_doc.get("completed_at")},
        "comments": comments,
        "results": items,
        "reviewKey": preset["review_key"],
    }

    report_title = f"CS307 forum comments - Clef classifications ({input_path.name})"
    report_sub = (f"Model: {results_doc.get('model')} | Run: {results_doc.get('completed_at')} | "
                  "Generated by generate_html.py")
    (REPO / preset["html"]).write_text(
        page(report_title, report_sub, REPORT_JS, blob), encoding="utf-8")

    review_title = f"Manual review: {input_path.name} (Clef: {results_doc.get('model')})"
    review_sub = ("Read each comment, pick the label you think is correct, mark borderline "
                  "calls, and explain disagreements. Clef's prediction is shown, not assumed.")
    (REPO / preset["review_page"]).write_text(
        page(review_title, review_sub, REVIEW_JS, blob), encoding="utf-8")

    ok = sum(1 for r in items if r.get("label") and not r.get("error"))
    print(f"wrote {preset['html']} and {preset['review_page']} "
          f"({len(comments)} comments, {ok} with a label, {len(items) - ok} failed)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    build_pages(args.dataset)


if __name__ == "__main__":
    main()
