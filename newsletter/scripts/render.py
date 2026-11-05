"""
Step 4 of the pipeline: combine newsletter_draft.json + meme.json into the
final HTML page and write it to newsletter/output/. This is what gets
published to GitHub Pages and is the link you drop in Sleeper chat / email.
"""

import json
import os
from config import PATHS

import html as _html
import re as _re


PAPER_NAME = "The Offensive Record"
PAPER_TAGLINE = "All the News That Isn't Defense"
TRADE_DEADLINE_WEEK = 14  # trades are not allowed after this week


def trade_deadline_notice(week_recapped):
    """Deterministic (not model-written): countdown from the week number."""
    next_week = week_recapped + 1
    dl = TRADE_DEADLINE_WEEK
    if next_week > dl:
        return f'<div class="deadline"><b>The trade deadline has passed.</b> Trades were not allowed after Week {dl}.</div>'
    base = f"<b>Trade Deadline: Week {dl}.</b> Trades are not allowed after Week {dl}."
    if next_week == dl:
        extra = " Next week is the last week to make a trade."
    else:
        n = dl - next_week
        extra = f" {n} week{'s' if n != 1 else ''} left after Week {next_week}."
    return f'<div class="deadline">{base}{extra}</div>'


def to_paragraphs(text):
    """Turn model text into one <p> per paragraph. The model separates
    paragraphs with blank lines, which HTML collapses if the whole string
    sits inside a single <p>. Falls back to single newlines if the model
    never used a blank line. Text is escaped so stray < or & can't break
    the page."""
    text = (text or "").strip()
    chunks = [c.strip() for c in _re.split(r"\n\s*\n", text) if c.strip()]
    if len(chunks) <= 1:
        chunks = [c.strip() for c in text.split("\n") if c.strip()]
    return "\n  ".join(f"<p>{_html.escape(c, quote=False)}</p>" for c in chunks)


def main():
    with open(PATHS["raw_data"]) as f:
        raw = json.load(f)
    with open(PATHS["newsletter_draft"]) as f:
        draft = json.load(f)
    with open(PATHS["meme"]) as f:
        meme = json.load(f)
    with open(PATHS["template"]) as f:
        template = f.read()

    rankings_html = "\n".join(
        f"      <li><b>{r['team']}</b> — {r['blurb']}</li>"
        for r in sorted(draft["power_rankings"], key=lambda x: x["rank"])
    )

    # Deterministic — built directly from raw data, not authored by the
    # model. It's a plain factual ordered list; there's no upside to
    # having an LLM write it and real risk of a wrong order or name.
    waiver_priority_html = "\n".join(
        f"      <li>{entry['priority']}. {entry['team_name']}</li>"
        for entry in raw.get("current_waiver_priority_order", [])
    )

    # Trade Desk only appears when there were trades — an empty section
    # header every week would be noise.
    trade_text = (draft.get("trade_desk") or "").strip()
    trade_section = f"<h2>Trade Desk</h2>\n  {to_paragraphs(trade_text)}" if trade_text and raw.get("trades_this_week") else ""

    html = (template
        .replace("{{PAPER_NAME}}", PAPER_NAME)
        .replace("{{PAPER_TAGLINE}}", PAPER_TAGLINE)
        .replace("{{TRADE_DEADLINE_NOTICE}}", trade_deadline_notice(int(raw["week_recapped"])))
        .replace("{{HEADLINE}}", draft["headline"])
        .replace("{{WEEK}}", str(raw["week_recapped"]))
        .replace("{{SEASON}}", str(raw["season"]))
        .replace("{{MEME_URL}}", meme["image_url"])
        .replace("{{RECAP}}", to_paragraphs(draft["recap"]))
        .replace("{{TRANSACTION_DESK}}", to_paragraphs(draft["transaction_desk"]))
        .replace("{{TRADE_DESK_SECTION}}", trade_section)
        .replace("{{POWER_RANKINGS_HTML}}", rankings_html)
        .replace("{{STANDINGS_NARRATIVE}}", to_paragraphs(draft["standings_narrative"]))
        .replace("{{WAIVER_PRIORITY_HTML}}", waiver_priority_html)
        .replace("{{LOOK_AHEAD}}", to_paragraphs(draft["look_ahead"]))
    )

    out_path = f"{PATHS['output_dir']}/{raw['season']}-wk{raw['week_recapped']:02d}.html"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)

    # Stable link that always points at the newest edition.
    with open(f"{PATHS['output_dir']}/latest.html", "w") as f:
        f.write(html)

    print(f"Newsletter published to {out_path}")


if __name__ == "__main__":
    main()
