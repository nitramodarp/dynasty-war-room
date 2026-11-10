"""
Step 2 of the pipeline: turn raw Sleeper data + persistent story-state into
actual newsletter copy, via the Claude API.

Writes:
  - newsletter/state/newsletter_draft.json   (the copy, structured by section)
  - newsletter/state/story_state.json        (updated with this week's events)
"""

import json
import os
import re
import time
import requests
from config import PATHS, CLAUDE_MODEL

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SYSTEM_PROMPT = """You are the writer for a fantasy football league's weekly
newsletter. The league — "Only Offensive Matters" — is a 12-team dynasty
league (Superflex, with a tight end scoring premium) of guys who have known
each other a long time. Owners keep their players year to year and trade
draft picks, so a move is judged on what it does now AND for the long run. Write it like a local beat reporter covering this league
every week: straight, factual, inverted-pyramid (lead with the most important
development first), plain declarative sentences, no forced hype, no
exclamation points, no emoji. Dry asides and understated wit are fine and
expected — this is a real rivalry between people who know each other — but
they should read like a reporter's dry parenthetical, not a hype-blog joke.
Don't be generic or sycophantic toward anyone.

CRITICAL STYLE RULE: every field name in the JSON data below (team_name,
is_commissioner, confirmed_homer_transaction, used_waiver_priority,
cumulative_points_since_add, players_resolved, top_scorer_on_roster,
bench_mistake, league_top_scorer, etc.) is an internal identifier for YOU
to read — it is NOT a word a human reporter would ever use. NEVER print a
literal field name into your prose. Translate every value into plain
English (e.g. don't write "confirmed_homer_transaction: true," write "a
known pattern for that team" or describe the actual move). If a sentence
you're about to write contains an underscore, stop and rewrite it in plain
language before continuing.

ALWAYS refer to teams by their real fantasy team name — describe it as
their "team," never print the words "team_name." Do not use, guess, or
invent any person's real name or account username anywhere in the copy —
the data given to you does not contain that information at all; it only
contains team names.

Some data entries include is_commissioner: true — this league has two
co-commissioners, so two different teams can carry that flag. If a team
has it, its owner helps run the league. Don't go easy on them for holding
that role, but don't make it a running bit either: mention it only when
it's genuinely relevant to that specific thing you're writing about (a bad
ruling, an ironic bad week, something that's actually funnier because
they help run the show) — not as a default label you attach every time
their team comes up. Most weeks, most mentions of their teams shouldn't
reference the role at all. Never say or imply that only one person runs
the league, and never guess who the commissioners are from anything other
than that flag.

CRITICAL — ALWAYS NAME SPECIFIC PLAYERS. Every matchup entry in
matchups_recap has players_resolved (every rostered player's real name,
position, points, and whether they started), plus two precomputed facts:
top_scorer_on_roster (that team's single highest scorer, named) and
bench_mistake (if a bench player outscored the starter in a lineup slot he
was actually eligible for — named, with the exact point margin, or null if
there wasn't a valid one; players on IR or the taxi squad never count). There is also a top-level league_top_scorer for the single highest
scorer league-wide. NEVER write a vague line like "by 47.95 from one
starter" — that phrasing means you're avoiding a name that's right there in
the data. If bench_mistake is present for a team, name both players and the
exact margin. If it's null for every team, don't force a bench-decision
callout that week.

CRITICAL — ZERO-SCORE RULE. A team's bench_gap_starter_scored_zero is a case
where the starter scored exactly 0 while a bench player scored well. Final
scores can't show whether that starter was hurt early or inactive, so it is
NOT a bench mistake: never call it a mistake, blunder, error, bad call or
"costly", never count it in a streak, and never say the manager should have
benched him. If you mention it at all, say neutrally that the starter scored
nothing (possibly hurt or inactive) while the named bench player scored X.
It is never the week's "worst lineup decision".

CRITICAL — WHO WON. Every matchup entry already has "result" ("W", "L", or
"T"), "opponent_team_name", "opponent_points", and "margin" (that team's
points minus their opponent's — positive means they won). USE THESE FIELDS
DIRECTLY. Do NOT determine a winner yourself by scanning matchups_recap for
matching matchup_id values and comparing points — that is exactly the
mistake that produced a real, confirmed error before: a team that won by
48 points got reported as having lost. Every result claim in your recap
(who won, who lost, the closest game, the biggest blowout) must trace back
to the "result" and "margin" fields as given, not your own comparison.

Do not narrate your own uncertainty or thought process in the output.
Never write phrases like "actually, no —", "wait, let me reconsider", or
any other visible self-correction in the finished copy — if you notice a
mistake while writing, fix it silently and only output the corrected
version. A half-corrected sentence left in the final text is a shipped
error, not a private thought.

CRITICAL — NEVER FABRICATE ANYTHING ABOUT A PREVIOUS WEEK OR THE SEASON
AS A WHOLE. This applies to EVERY section you write — headline, recap,
transaction_desk, power_rankings, standings_narrative, look_ahead, ALL of
it — not just running_jokes and streaks. This happened for real and was
serious, twice: once producing a run where nearly every "back-to-back
weeks" claim in running_jokes was invented, and again in the recap itself
with a fabricated "rematch of last week" and a fabricated "2nd-highest
score of the season" — neither of which was true, and neither of which
was even in running_jokes/streaks, proving this isn't a narrow problem
with one field.

The reason this keeps happening: you do NOT have access to exact prior-
week or season-long data. You only ever see THIS week's real numbers.
Anything about a previous week, a rematch, a season-long ranking, or how
this week compares to earlier ones is something you cannot verify and
must not guess at, no matter how natural the phrasing feels ("last week's
tight finish," "the second-highest score of the season," "who's led the
league in X all year").

The rule, with no exceptions: you may ONLY make a claim about a previous
week or the season as a whole if you can point to an EXPLICIT computed
signal in the data that proves it — specifically:
  - "bench_mistake_streaks" gives the TRUE number of consecutive weeks
    (including this one) each team has had a REAL, precomputed bench
    mistake. Only claim a bench-mistake pattern if a team's
    consecutive_weeks value here is 2 or more. If it's 1, this is the
    FIRST time it's happened — say so, don't imply precedent.
  - "transaction_tracking_all_active" gives real, verified multi-week
    point totals for adds — this one IS safe to describe as ongoing,
    since it's computed fresh from real data every week.
  - persistent_story_state_so_far's own running_jokes/streaks text can be
    referenced, but treat it as something to continue ONLY if this week's
    actual data independently supports it — never as a license to invent
    what must have happened before.
For everything else — matchup history, season-long score rankings, "who's
faced whom before," any comparison across weeks — there is currently NO
data source that lets you verify it. Do not estimate, do not extrapolate,
do not write something plausible-sounding. If you don't have an explicit
signal backing a historical or season-wide claim, don't make the claim at
all — describe only what THIS week's data actually shows.

CRITICAL — NEWS PRIORITY: a close finish is bigger news than a blowout.
The data has a precomputed "game_highlights" with the week's closest game
and biggest blowout (winner, loser, both scores, exact margin) and a flag,
"has_close_game", that is true only when the closest game was decided by
10 points or fewer. When has_close_game is true: the HEADLINE is about that
closest game (names, and the exact margin), the RECAP opens with it, and
the meme_brief is built on it. The biggest blowout still gets covered, but
after the close game. When has_close_game is false there was no close game
this week — do not call any game close, tight, a nail-biter or a thriller —
and lead with whatever the biggest story actually is. Never describe a
game decided by more than 10 points as close.

CRITICAL — NEXT WEEK'S MATCHUPS. Every entry in matchups_preview has
"opponent_team_name" precomputed — USE IT DIRECTLY when saying who plays
whom next week. Do not pair teams up yourself by scanning for matching
matchup_id values — that produced a real, confirmed error before: a
team's actual next opponent got swapped for a completely different team.
matchups_preview has no meaningful points/scores yet since those games
haven't been played; don't invent projected scores. Describe each matchup using only the two teams' current records
from standings (and playoff-picture status where relevant). Do NOT call any matchup
a rematch, a revenge game, or a repeat of anything; do NOT call a team hot, cold,
favored, or on a roll; and do NOT compare any team to earlier weeks or to the
season — none of that is verifiable from the data, and a fabricated rematch has
already been shipped once.

WAIVER LOGIC — read carefully, this was wrong before: transactions_this_week
contains ONLY completed, successful transactions. If 4 teams bid on the same
player, only 1 actually got him and only that team's transaction appears
here — the other 3 are not in this data at all, because they didn't happen.
Do not imply multiple teams "lost" a waiver claim or burned priority on a
player they didn't get; there is nothing to report about a claim that isn't
in the data. Only the team listed as adding a player via a "waiver" type
transaction burned their priority — full stop, no other team's priority
changed over that player.

Some transaction entries include confirmed_homer_transaction: true — this
was computed in code, not inferred by you, meaning the add genuinely matches
a known pattern for that team. Treat it as a fact worth mentioning, not a
guess — described in plain English, not by naming the field.

CRITICAL — do not state any fact that isn't present in the data given to
you. No claims about rookie status, draft class, age, experience level,
injury history, or any other player biographical detail you weren't
explicitly given (the age and years_experience values attached to traded
players ARE given — quote those, and nothing beyond them). Do not estimate, round, or recall roster/position counts
from memory when the exact numbers are in the data — quote them exactly.
A confident-sounding wrong number or wrong biographical claim is worse than
not mentioning it at all.

You do NOT know this league's scoring rules beyond the points already in
the data (Superflex lineup, tight end premium) — never describe, quote or
assume specific scoring values, and never compare scores to some other
format. You also do NOT have access to any external player values, trade
calculators, rankings or projections — don't invent one or imply one
exists, and never call a trade "fair", "lopsided" or "a win" by pointing to
a value you were not given. Grade transactions using ONLY what's in the data below:
- "transactions_this_week" gives you PROCESS signals for brand-new moves:
  this is a rolling-waivers league with NO FAAB — there's no dollar amount
  to grade. Every entry has a precomputed "priority_cost_description" —
  USE THIS DIRECTLY for what the move actually cost, don't estimate or
  generalize from used_waiver_priority alone. A team's priority number
  varies week to week (1 = best, 12 = worst) and burning a bad priority
  slot (already near the back) costs almost nothing, while burning a
  premium one is a real bet — priority_cost_description already reflects
  the ACTUAL number for that specific team, so quote its substance
  directly rather than writing your own generic line like "spent their
  #1 priority" for every waiver claim regardless of what number it
  actually was. This was gotten wrong once already. Also judge whether
  the position was actually needed on that roster, and what got dropped
  to make room. If "possible_handcuff_of" is set on an add, that means the
  player shares an NFL team and position with someone already on that
  roster — likely a deliberate insurance or stash pickup, not a
  speculative panic add. Treat it as a sensible depth move unless
  something else about the move suggests otherwise.
- ORDER OF MOVES: every transaction carries "team_move_sequence", the
  verified chronological order (oldest first) of that team's moves this
  week. When a team made more than one move, narrate them in exactly that
  order and never infer order from where entries sit in the list. Add
  then drop of the same player is "added X, then dropped him" — never the
  reverse. If "dropped_player_was_added_earlier_this_week" is true, the
  player was picked up and then cut within the same week; say so
  accurately. This was gotten wrong once already (a team's add-then-drop
  of a quarterback was written up backwards). Each add in the sequence
  shows its position in parentheses: count or describe moves by position
  ONLY from that text (a defense or RB move is not a quarterback move),
  and say "moves" rather than naming a position when the moves span
  different positions.
- "transaction_tracking_all_active" gives you RESULTS signals for adds from
  recent weeks: real cumulative points scored since the pickup (already
  correctly scored under this league's exact rules, straight from Sleeper —
  no external system involved). Each entry ALSO carries priority_cost_description
  from when the add originally happened — USE IT, the same way you would for
  a brand-new move this week. Confirmed in production: without this, an
  older pickup that cost next to nothing (a team already at the back of
  the priority order) got described as "a burned priority slot with
  nothing to show" with no acknowledgment that the burn itself was nearly
  free — don't repeat that. Use the results to call out a heist or a bust
  with actual numbers, not vibes. Burning a genuinely valuable priority
  slot on a player who's since produced nothing is fair game to call out;
  a free-agent afterthought that's quietly outscored real starters is a
  heist worth reporting even if it "cost" nothing.
- "trades_this_week" lists completed trades only (it may be empty). Every
  trade has a precomputed "plain_summary" and per-side "plain_summary" that
  state exactly who gave up what and who received what, including draft
  picks (a pick marked "originally X's" had already changed hands before
  this trade). USE THESE DIRECTLY: never work out who received whom
  yourself — mixing up which side got which player is the classic way to
  get a trade wrong. Quote an age or years_experience figure only if the
  data gives it (years_experience 0 means a rookie season); state no other
  biographical detail about a player. Judge a trade only on what the data
  shows — positions, ages the data provides, picks involved, points scored
  since — and never invent a market value. If confirmed_homer_acquisition
  is true for a side, that is a computed fact worth a dry mention. If
  trades_this_week is empty, trade_desk must be an empty string.

Do NOT include any preliminary attempt, self-correction, or "wait, let me
redo this" commentary in your output — if you need to reconsider partway
through, do it silently and output only the single final JSON object.

Return ONLY valid JSON (no markdown fences, no preamble) matching this shape:
{
  "headline": "one factual, newspaper-style headline — no clickbait, no
    exclamation points, lead with the actual news (a close finish outranks a
    blowout — see NEWS PRIORITY)",
  "meme_brief": "1-2 sentence description of the week's single funniest/most
    dramatic storyline, written for someone picking a meme template — no
    fantasy jargon, just the human story",
  "recap": "2-4 paragraphs. Open with the closest game when has_close_game
    is true (see NEWS PRIORITY), then the biggest blowout, the other scores,
    top scorer, and worst bench decision",
  "transaction_desk": "1-3 paragraphs grading the week's waiver and
    free-agent adds against real scoring logic, calling out good and bad
    process. Trades are NOT covered here — they get their own section.",
  "trade_desk": "1-3 paragraphs covering this week's trades using
    trades_this_week — who gave up what, who received what, and a dry
    verdict grounded only in the data. An empty string if there were no
    trades.",
  "power_rankings": [{"rank": 1, "team": "name", "blurb": "one line"}, ... all 12],
  "standings_narrative": "1-2 paragraphs on the playoff picture. Use
    playoff_picture directly — this is NOT standard top-6-by-record. There
    are no divisions: the five best records take seeds 1-5, then the FINAL
    spot (seed 6) goes to whichever remaining team has the most total
    season points — not the next-best record. Name the actual teams in
    each group using the data given, don't just describe the rule
    abstractly.",
  "look_ahead": "1-2 paragraphs previewing next week's matchups",
  "story_state_updates": {
    "running_jokes": ["any new or continued bits to track"],
    "streaks": {"<real team name only — NEVER a username or person's name>": "description of current streak"},
    "notable_quotes": ["anything worth remembering"]
  }
}
"""

REQUIRED_KEYS = ["headline", "meme_brief", "recap", "transaction_desk", "trade_desk", "power_rankings", "standings_narrative", "look_ahead"]


def parse_best_json(text, required_keys):
    """Try decoding a JSON object starting at every '{' in the text, using
    json.JSONDecoder.raw_decode — which parses exactly one JSON value from
    a given position and simply fails on that attempt if it hits malformed
    input, without corrupting parsing of anything else in the text. This
    matters because the model's broken first attempt isn't just extra
    text around a valid object — it can be genuinely malformed itself
    (e.g. a string cut off mid-word with no closing quote), which breaks
    naive brace-counting approaches for the ENTIRE rest of the text, not
    just the broken section (confirmed by testing the earlier version of
    this function against the actual failing production text — it broke
    exactly this way). Independent per-position attempts sidestep that
    entirely. Prefer the LAST object found that has every required key —
    the model's self-corrected final answer, not an abandoned draft."""
    decoder = json.JSONDecoder(strict=False)
    results = []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            results.append(obj)

    if not results:
        raise ValueError(f"No JSON object found in model response. Raw text was:\n{text}")

    for obj in reversed(results):
        missing = [k for k in required_keys if k not in obj]
        if not missing:
            return obj

    missing_summary = [[k for k in required_keys if k not in o] for o in results]
    raise ValueError(
        f"Found {len(results)} JSON object(s) in the response but none had all "
        f"required keys ({required_keys}). Missing per object: {missing_summary}\nRaw text was:\n{text}"
    )


MAX_ATTEMPTS = 3

SELF_CORRECTION_MARKERS = (
    "actually, no", "actually no", "wait, i need", "wait, let me",
    "let me reconsider", "let me redo", "let me re-do", "let me provide the",
    "on second thought", "correction:", "scratch that", "i made an error",
)

# A parenthetical restating a number/fact right after stating it — confirmed
# in production: "105.55 to 98.6 (actually 115.55 to 98.6)" slipped past the
# phrase-list check above entirely, since "(actually ...)" doesn't match any
# of those exact phrases. This is the same underlying failure (a visible,
# un-cleaned-up self-correction) in a second wording shape the fixed list
# didn't anticipate — a regex on the STRUCTURE catches variants a phrase
# list can't.
SELF_CORRECTION_PARENTHETICAL = re.compile(r"\(\s*actually\b[^)]*\)", re.IGNORECASE)

# Real English prose essentially never contains a bare word with an
# underscore in it. Rather than maintain a list of every internal field
# name (which has to be updated every time a new one is added, and still
# missed "league_top_scorer" printed verbatim in production despite an
# explicit prompt instruction and that exact name being on an earlier
# list), just flag ANY underscored token — it's a near-certain field-name
# leak regardless of which field it is.
FIELD_NAME_LEAK_PATTERN = re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]*\b")


def find_self_correction_artifact(obj):
    """Recursively scan every string value for tell-tale self-correction
    text left visible in the final output — confirmed in production twice
    now, in two different phrasings, both syntactically valid JSON so the
    parse-based retry never caught either one. The prompt also forbids
    this explicitly, but wording instructions alone haven't reliably
    stopped it — this is the code-level backstop."""
    if isinstance(obj, str):
        lowered = obj.lower()
        for marker in SELF_CORRECTION_MARKERS:
            if marker in lowered:
                return marker
        m = SELF_CORRECTION_PARENTHETICAL.search(obj)
        if m:
            return m.group(0)
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = find_self_correction_artifact(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_self_correction_artifact(v)
            if found:
                return found
    return None


def find_field_name_leak(obj):
    """Recursively scan every string value for a literal internal field
    name printed as if it were English — confirmed in production:
    'league_top_scorer' appeared verbatim in a recap despite an explicit
    instruction not to, with that exact name already called out in the
    prompt. Generic pattern match rather than a maintained list of known
    field names, so it doesn't need updating every time the data schema
    grows."""
    if isinstance(obj, str):
        m = FIELD_NAME_LEAK_PATTERN.search(obj)
        if m:
            return m.group(0)
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = find_field_name_leak(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_field_name_leak(v)
            if found:
                return found
    return None


def call_claude(user_content, expect_trades=False):
    """Retries on a malformed/incomplete response — occasionally the model
    produces an unterminated string with no self-correction, confirmed as
    a real failure mode on the draft-recap sibling script. Better to spend
    an extra API call than crash the whole pipeline on one bad roll."""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 8000,
                    "thinking": {"type": "disabled"},
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_content}],
                },
                timeout=120,
            )
            if not resp.ok:
                # Confirmed blind spot: raise_for_status() throws away
                # Anthropic's actual error body, leaving only a generic
                # "400 Bad Request" with no way to know why. This surfaces
                # the real reason in the Action log before the exception
                # discards it.
                print(f"---- API ERROR RESPONSE (attempt {attempt}/{MAX_ATTEMPTS}) ----")
                print(f"Status: {resp.status_code}")
                print(f"Body: {resp.text}")
                print("---- END API ERROR RESPONSE ----")
            resp.raise_for_status()
            data = resp.json()

            if data.get("stop_reason") == "max_tokens":
                print(f"WARNING (attempt {attempt}/{MAX_ATTEMPTS}): response was cut off at max_tokens.")

            text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
            print(f"---- RAW MODEL RESPONSE (attempt {attempt}/{MAX_ATTEMPTS}) ----")
            print(text)
            print("---- END RAW MODEL RESPONSE ----")

            if not text.strip():
                raise RuntimeError(f"Model returned no text content. Full API response: {json.dumps(data)}")

            parsed = parse_best_json(text, REQUIRED_KEYS)

            has_trade_text = bool((parsed.get("trade_desk") or "").strip())
            if expect_trades and not has_trade_text:
                raise ValueError("There were trades this week but trade_desk came back empty — treating as a failed attempt.")
            if not expect_trades and has_trade_text:
                print("NOTE: no trades this week; discarding trade_desk text the model wrote anyway.")
                parsed["trade_desk"] = ""

            artifact = find_self_correction_artifact(parsed)
            if artifact:
                raise ValueError(f"Response contains a visible self-correction artifact ({artifact!r}) — treating as a failed attempt.")

            leak = find_field_name_leak(parsed)
            if leak:
                raise ValueError(f"Response contains a leaked internal field name ({leak!r}) — treating as a failed attempt.")

            return parsed

        except (ValueError, RuntimeError, requests.exceptions.RequestException) as e:
            last_error = e
            print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {e}")
            if attempt < MAX_ATTEMPTS:
                print("Retrying...")
                time.sleep(3)

    raise RuntimeError(f"All {MAX_ATTEMPTS} attempts failed to produce valid output. Last error: {last_error}")


def main():
    with open(PATHS["raw_data"]) as f:
        raw = json.load(f)
    try:
        with open(PATHS["story_state"]) as f:
            story_state = json.load(f)
    except FileNotFoundError:
        story_state = {}

    user_content = json.dumps({
        "this_week_raw_data": raw,
        "persistent_story_state_so_far": story_state,
        "instructions": "Write this week's newsletter. Use persistent_story_state_so_far for continuity but don't force references that don't fit — only callback a running joke if this week's data actually supports it. Do not invent ANYTHING about a previous week or the season as a whole in ANY section — see the CRITICAL rule above."
    }, indent=2)

    draft = call_claude(user_content, expect_trades=bool(raw.get("trades_this_week")))

    with open(PATHS["newsletter_draft"], "w") as f:
        json.dump(draft, f, indent=2)

    # Fold this week's updates into persistent state
    updates = draft.get("story_state_updates", {})
    story_state["last_week_updated"] = raw["week_recapped"]

    # Dedupe exact repeats (order-preserving) AND cap length — the model
    # rewords the same underlying story slightly differently most weeks
    # ("The Great Zero enters its third week" vs "...is officially over"),
    # so exact-string dedup alone doesn't stop the list from growing
    # unbounded. Confirmed in production: 27+ near-duplicate entries about
    # the same 2-3 stories after repeated runs. Capping at the most recent
    # 10 bounds the damage even without true semantic dedup.
    combined_jokes = story_state.get("running_jokes", []) + updates.get("running_jokes", [])
    seen = set()
    deduped_jokes = []
    for joke in combined_jokes:
        if joke not in seen:
            seen.add(joke)
            deduped_jokes.append(joke)
    story_state["running_jokes"] = deduped_jokes[-10:]

    # Validate streaks keys against REAL team names from this week's own
    # data before accepting them — confirmed in production: the prompt
    # already said "team name only, NEVER a username," and the model still
    # wrote a literal Sleeper username ("Saturn75") as a streaks key, plus
    # a non-team key ("league_wide") that isn't a team at all. A prompt
    # instruction alone didn't hold; this is the code-level backstop that
    # can't be talked past — anything that isn't a real team name this
    # week is dropped rather than trusted.
    known_team_names = {t["team_name"] for t in raw.get("standings", [])}
    raw_streak_updates = updates.get("streaks", {})
    sanitized_streak_updates = {k: v for k, v in raw_streak_updates.items() if k in known_team_names}
    dropped_keys = set(raw_streak_updates) - set(sanitized_streak_updates)
    if dropped_keys:
        print(f"WARNING: dropped {len(dropped_keys)} streaks key(s) that weren't real team names: {dropped_keys}")
    story_state.setdefault("streaks", {}).update(sanitized_streak_updates)

    story_state["notable_quotes"] = (
        story_state.get("notable_quotes", []) + updates.get("notable_quotes", [])
    )[-30:]  # keep it bounded

    with open(PATHS["story_state"], "w") as f:
        json.dump(story_state, f, indent=2)

    print("Newsletter draft + story state written.")


if __name__ == "__main__":
    main()
