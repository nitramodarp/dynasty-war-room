"""
Step 1 of the pipeline: pull everything the newsletter needs from Sleeper's
read-only API and write it to newsletter/state/raw_week_data.json.

Dynasty league edition. IMPORTANT: this pipeline uses ONLY Sleeper-native
data. No external values/rankings (KTC, the war room app's value sources)
are referenced anywhere here — the repo is public. Sleeper's own weekly
player points already reflect this league's exact scoring settings, and
transaction grading is done retrospectively off real scored points.
"""

import json
import os
import time
import requests
from config import LEAGUE_ID, OWNER_MAP, PATHS, COMMISSIONER_OWNER_USERNAME, OWNER_NOTES, resolve_player_name, NFL_TEAM_NAMES

BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_PATH = "newsletter/state/players_cache.json"
PLAYERS_CACHE_MAX_AGE_DAYS = 7
TRANSACTION_TRACKING_WINDOW_WEEKS = 4  # how many weeks to keep tallying an add's output


def get(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def get_nfl_state():
    return get(f"{BASE}/state/nfl")


def get_league():
    return get(f"{BASE}/league/{LEAGUE_ID}")


def get_rosters():
    return get(f"{BASE}/league/{LEAGUE_ID}/rosters")


def get_users():
    return get(f"{BASE}/league/{LEAGUE_ID}/users")


def get_matchups(week):
    return get(f"{BASE}/league/{LEAGUE_ID}/matchups/{week}")


def get_transactions(week):
    return get(f"{BASE}/league/{LEAGUE_ID}/transactions/{week}")


def get_players_cached():
    """The full Sleeper player DB is ~5MB — cache it locally instead of
    pulling it every single run. Only used for position/name lookups when
    grading transactions, never for scoring (Sleeper already scores)."""
    if os.path.exists(PLAYERS_CACHE_PATH):
        age_days = (time.time() - os.path.getmtime(PLAYERS_CACHE_PATH)) / 86400
        if age_days < PLAYERS_CACHE_MAX_AGE_DAYS:
            with open(PLAYERS_CACHE_PATH) as f:
                return json.load(f)
    players = get(f"{BASE}/players/nfl")
    with open(PLAYERS_CACHE_PATH, "w") as f:
        json.dump(players, f)
    return players


def roster_id_to_team_info(rosters, users):
    """Map roster_id -> {team_name, owner_username, is_commissioner}.

    team_name is the REAL fantasy team name — Sleeper stores it on each
    user's league-specific metadata.team_name — and should ALWAYS be what
    shows up in newsletter copy. owner_username (OWNER_MAP, falling back to
    Sleeper's display_name) is kept only as an internal key for matching
    OWNER_NOTES tendencies; it should never be printed in the output."""
    user_by_id = {u["user_id"]: u for u in users}
    out = {}
    for r in rosters:
        owner_id = r.get("owner_id")
        user = user_by_id.get(owner_id, {})
        owner_username = OWNER_MAP.get(owner_id) or user.get("display_name", "Unknown")
        team_name = (user.get("metadata") or {}).get("team_name") or owner_username
        out[r["roster_id"]] = {
            "owner_id": owner_id,
            "owner_username": owner_username,
            "team_name": team_name,
            "is_commissioner": owner_username == COMMISSIONER_OWNER_USERNAME,
        }
    return out


def win_pct(t):
    games = t["wins"] + t["losses"] + t["ties"]
    return (t["wins"] + 0.5 * t["ties"]) / games if games else 0.0


def build_standings(rosters, roster_names):
    standings = []
    for r in rosters:
        s = r.get("settings", {})
        standings.append({
            "roster_id": r["roster_id"],
            "team_name": roster_names[r["roster_id"]]["team_name"],
            "is_commissioner": roster_names[r["roster_id"]]["is_commissioner"],
            "wins": s.get("wins", 0),
            "losses": s.get("losses", 0),
            "ties": s.get("ties", 0),
            "fpts": s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100,
            "fpts_against": s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100,
        })
    # Record first (ties count half), then total points scored as the tiebreak.
    standings.sort(key=lambda x: (-win_pct(x), -x["fpts"]))
    return standings


def compute_playoff_picture(standings):
    """Confirmed format for this league: no divisions. The top 5 teams by
    win-loss-tie record take seeds 1-5; the 6th and final spot goes to
    whichever of the REMAINING teams has the most total points scored on
    the season (not the next-best record). Computed here rather than left
    to the model — it's an unusual multi-step rule, exactly the kind of
    thing that silently turns into a standard top-6-by-record assumption."""
    top_5 = [{**t, "seed": i} for i, t in enumerate(standings[:5], 1)]
    fifth = standings[4] if len(standings) >= 5 else None
    remaining = sorted(standings[5:], key=lambda t: -t["fpts"])

    def annotate(t):
        out = dict(t)
        if fifth:
            # wins-behind the 5th seed: half the sum of the win gap and loss gap
            out["games_behind_fifth_seed"] = ((fifth["wins"] - t["wins"]) + (t["losses"] - fifth["losses"])) / 2
        return out

    sixth = annotate(remaining[0]) if remaining else None
    if sixth:
        sixth["seed"] = 6
    bubble = annotate(remaining[1]) if len(remaining) > 1 else None
    outside = [annotate(t) for t in remaining[2:]]
    if sixth and bubble:
        sixth["points_lead_over_next_team_in_line"] = round(sixth["fpts"] - bubble["fpts"], 2)
    return {
        "note": "Playoffs: no divisions. Seeds 1-5 are the five best records; "
                "seed 6 goes to the team with the most total season points "
                "among everyone else = 6 teams.",
        "seeds_1_to_5_by_record": top_5,
        "sixth_seed_by_points": sixth,
        "on_the_bubble_by_points": bubble,
        "teams_outside_playoff_picture": outside,
    }


def determine_weeks(nfl_state):
    current = nfl_state["week"]
    return max(current - 1, 1), current


# Which player positions may fill each Sleeper roster slot. The league's own
# roster_positions list decides which slots exist (Superflex, TE premium
# leagues, a DEF slot or not...) — nothing about the lineup is assumed here.
SLOT_ELIGIBILITY = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}


def attach_opponent_names(matchups):
    """For PREVIEW matchups (not yet played) — pairs matchup_id groups and
    attaches just the opponent's team name. Points/result would be
    meaningless here since the games haven't happened, so this is
    deliberately lighter than compute_matchup_results. Same root cause as
    that function though, confirmed in production: the model had to group
    a flat list of 12 entries by matchup_id itself to figure out who's
    playing whom next week, and got a pairing wrong (attributed a team's
    upcoming opponent incorrectly). Precomputing the pairing removes that
    inference step entirely."""
    by_matchup_id = {}
    for m in matchups:
        by_matchup_id.setdefault(m.get("matchup_id"), []).append(m)
    for mid, pair in by_matchup_id.items():
        if len(pair) != 2:
            for m in pair:
                m["opponent_team_name"] = None  # bye week, or unexpected pairing — don't guess
            continue
        a, b = pair
        a["opponent_team_name"] = b["team_name"]
        b["opponent_team_name"] = a["team_name"]
    return matchups


def compute_matchup_results(matchups):
    """Precomputed W/L/T, opponent, and margin for every team — added
    after a confirmed real error: the model inverted an actual result
    (called a decisive win a loss) rather than correctly grouping 12 flat
    matchup entries by matchup_id and comparing points itself. That's
    exactly the kind of multi-step inference that's failed repeatedly in
    this pipeline; the fix every other time has been the same: stop
    asking the model to derive the fact, hand over the finished one."""
    by_matchup_id = {}
    for m in matchups:
        by_matchup_id.setdefault(m.get("matchup_id"), []).append(m)

    for mid, pair in by_matchup_id.items():
        if len(pair) != 2:
            # Bye week, or a matchup_id Sleeper didn't pair as expected —
            # leave result fields unset rather than guess.
            for m in pair:
                m["opponent_team_name"] = None
                m["opponent_points"] = None
                m["result"] = None
                m["margin"] = None
            continue
        a, b = pair
        a_pts, b_pts = a.get("points", 0) or 0, b.get("points", 0) or 0
        for team, opp, team_pts, opp_pts in ((a, b, a_pts, b_pts), (b, a, b_pts, a_pts)):
            team["opponent_team_name"] = opp["team_name"]
            team["opponent_points"] = opp_pts
            team["margin"] = round(team_pts - opp_pts, 2)
            if team_pts > opp_pts:
                team["result"] = "W"
            elif team_pts < opp_pts:
                team["result"] = "L"
            else:
                team["result"] = "T"
    return matchups


def enrich_matchup_players(matchups, players_db, roster_positions, rosters_by_id):
    """Resolve raw player_id -> name/position for every rostered player in
    each matchup entry (Sleeper's players_points is only {player_id:
    points}, no names at all), then precompute the facts the newsletter
    needs names for: each roster's single top scorer, and the single
    biggest real lineup mistake (a bench player who outscored the starter
    in a slot he was eligible for).

    The bench-mistake check is exact, not an approximation: Sleeper's
    `starters` array lines up one-to-one with the league's non-bench
    roster_positions, so every starter's actual slot is known. A bench
    player is a mistake candidate for a slot if his position is allowed
    in that slot and he outscored whoever started there. Players on IR or
    the taxi squad can't be started, so they're never candidates. If the
    slot list and the starters array don't line up, no mistake is claimed
    for that matchup rather than guessing."""
    slots = [p for p in (roster_positions or []) if p != "BN"]
    mismatches = 0
    for m in matchups:
        players_points = m.get("players_points") or {}
        starters_list = list(m.get("starters") or [])
        starters = set(starters_list)
        roster = rosters_by_id.get(m.get("roster_id"), {})
        unavailable = set(map(str, (roster.get("reserve") or []) + (roster.get("taxi") or [])))

        resolved = []
        for pid, pts in players_points.items():
            player = players_db.get(str(pid), {})
            position = player.get("position") or ("DEF" if pid in NFL_TEAM_NAMES else "UNK")
            resolved.append({
                "player_id": pid,
                "name": resolve_player_name(pid, players_db),
                "position": position,
                "points": pts or 0,
                "started": pid in starters,
            })
        by_id = {p["player_id"]: p for p in resolved}
        top_scorer = max(resolved, key=lambda p: p["points"], default=None)

        bench_mistake = None
        if len(starters_list) == len(slots):
            bench_entries = [p for p in resolved
                             if not p["started"] and str(p["player_id"]) not in unavailable]
            for slot, pid in zip(slots, starters_list):
                eligible = SLOT_ELIGIBILITY.get(slot)
                starter = by_id.get(pid)
                if not eligible or not starter:
                    continue  # unknown slot type or empty slot — make no claim
                for b in bench_entries:
                    if b["position"] in eligible and b["points"] > starter["points"]:
                        gap = b["points"] - starter["points"]
                        if not bench_mistake or gap > bench_mistake["margin"]:
                            bench_mistake = {
                                "benched_player": b["name"], "benched_points": b["points"],
                                "started_instead": starter["name"], "started_points": starter["points"],
                                "margin": round(gap, 2),
                            }
        else:
            mismatches += 1

        # Keep what the model can use: starters, plus anyone who scored.
        m["players_resolved"] = [p for p in resolved if p["started"] or p["points"]]
        m["top_scorer_on_roster"] = top_scorer
        m["bench_mistake"] = bench_mistake  # null if no valid swap found
    if mismatches:
        print(f"WARNING: {mismatches} matchup(s) had a starters list that doesn't match the "
              f"league's {len(slots)} starting slots — no bench mistake claimed for those.")
    return matchups


def strip_raw_ids(matchups):
    """The model only needs names; the raw id lists/maps are pure token
    weight. Called after everything that reads them has already run."""
    for m in matchups:
        for key in ("players", "starters", "players_points", "starters_points", "custom_points"):
            m.pop(key, None)
    return matchups


def compute_league_top_scorer(matchups):
    """Single highest-scoring STARTER across the whole league this week —
    restricted to starters since a huge outlier sitting on a bench is
    already covered by bench_mistake, not "the" story on its own."""
    best = None
    for m in matchups:
        for p in m.get("players_resolved", []):
            if p["started"] and (best is None or p["points"] > best["points"]):
                best = {**p, "team_name": m.get("team_name")}
    return best


def update_bench_mistake_streaks(story_state, matchups_recap, week):
    """Deterministic per-team count of CONSECUTIVE weeks with a real,
    precomputed bench_mistake — added after a confirmed, serious bug: the
    model invented a nonexistent Week 1 bench-mistake precedent to make a
    real Week 2 event sound like a "back-to-back weeks" pattern, and that
    fabrication got written into persistent story_state as if it were
    verified fact. Checked directly against Week 1's actual published
    newsletter: EVERY multi-week claim generated that week (4 separate
    running_jokes entries, covering two different teams and one entirely
    fictional repeat matchup) turned out to be invented — this wasn't a
    one-off slip, it was systemic. Never again: the model gets a TRUE
    streak length computed here, and is told not to claim a multi-week
    pattern without one."""
    streaks = story_state.setdefault("bench_mistake_streaks", {})
    for m in matchups_recap:
        team = m.get("team_name")
        mistake = m.get("bench_mistake")
        existing = streaks.get(team)
        if mistake:
            if existing and existing.get("last_week_counted") == week - 1:
                consecutive = existing["consecutive_weeks"] + 1
            else:
                consecutive = 1
            streaks[team] = {
                "consecutive_weeks": consecutive,
                "last_week_counted": week,
                "last_mistake": mistake,
            }
        elif existing:
            # Had a streak, none this week — record that it's broken
            # rather than leaving stale data that implies it's ongoing.
            streaks[team] = {"consecutive_weeks": 0, "last_week_counted": week, "last_mistake": None}
    return streaks


def roster_position_counts(roster, players_db):
    """How many players at each position a roster carried BEFORE this
    week's moves — used to judge whether an add addressed a real need or
    was just a name-brand panic add at an already-stacked position."""
    counts = {}
    for pid in roster.get("players") or []:
        pos = players_db.get(str(pid), {}).get("position", "UNK")
        counts[pos] = counts.get(pos, 0) + 1
    return counts


def find_handcuff_teammate(added_player_id, added_player, roster, players_db):
    """If the added player shares an NFL team AND position with someone
    already on this fantasy roster, that's very likely a handcuff /
    injury-insurance pickup — not a speculative add. We have no external
    scouting or depth-chart source in this pipeline, but this signal is
    fully derivable from Sleeper's own roster + player data: added after
    a real add (Malik Davis, Javonte Williams' backup) got read as a
    low-value speculative move when it was actually a deliberate handcuff.
    Only catches same-team/same-position — doesn't know WHICH one is the
    lead back, so the model still has to reason about that direction."""
    added_team = added_player.get("team")
    added_position = added_player.get("position")
    if not added_team or not added_position:
        return None
    for pid in roster.get("players") or []:
        if str(pid) == str(added_player_id):
            continue
        teammate = players_db.get(str(pid), {})
        if teammate.get("team") == added_team and teammate.get("position") == added_position:
            return teammate.get("full_name") or resolve_player_name(pid, players_db)
    return None


def describe_priority_cost(used_priority, priority_before, team_count):
    """Precomputed, unambiguous description of what a waiver claim actually
    cost — added after the model latched onto an illustrative example
    phrase ('spending its #1 priority slot') and started attaching it to
    every waiver claim regardless of the team's real priority number. A
    team burning priority 12 of 12 (already last) gave up almost nothing;
    a team burning priority 1 of 12 gave up a real, valuable asset. Same
    fix pattern as market_verdict and roster_lean: hand over the finished
    conclusion, don't ask the model to correctly interpret a raw number."""
    if not used_priority:
        return "no priority spent — an uncontested free-agent pickup, nobody else wanted him"
    if priority_before is None:
        return "used a waiver claim, but this team's priority position before the move isn't available"
    if priority_before <= max(1, team_count // 4):
        return f"spent a premium priority slot (was #{priority_before} of {team_count}) — a real, costly bet"
    if priority_before >= team_count - max(1, team_count // 4) + 1:
        return f"was already near the back of the priority order (#{priority_before} of {team_count}) — burning it cost next to nothing"
    return f"spent a mid-tier priority slot (#{priority_before} of {team_count})"


def enrich_transactions(transactions, players_db, roster_names, rosters_by_id, story_state):
    """Attach names, positions, and pre-move roster construction to each
    transaction — all sourced from Sleeper, nothing external.

    Waiver and free-agent moves only — trades go through enrich_trades().

    This is a rolling-waivers league (no FAAB). The cost signal here is
    priority, not budget: a 'waiver' transaction burns a claim and sends
    that roster to the back of the priority order; a 'free_agent'
    transaction is a free, no-cost pickup because no one else wanted the
    player badly enough to claim him. We track waiver_position week over
    week in story_state so we can say "spent his #1 priority slot on a
    guy who's since done nothing" rather than a dollar amount.

    CRITICAL: only status == "complete" transactions are processed. Sleeper's
    transactions endpoint also returns FAILED waiver claims (e.g. all 4
    teams that bid on a player, not just the winner) — including those
    made it look like 4 teams each burned priority on the same player,
    when really 3 of them just... didn't get him. Only the winning claim
    actually happened and only it burns priority.
    """
    prev_priority = story_state.get("last_known_waiver_priority", {})
    enriched = []
    skipped_incomplete = 0
    # Sleeper returns transactions newest-first. Sort oldest-first so every
    # downstream consumer (including the model) sees real chronological order.
    transactions = sorted(
        transactions, key=lambda t: t.get("created") or t.get("status_updated") or 0
    )
    for t in transactions:
        if t.get("type") not in ("waiver", "free_agent"):
            continue  # trades are handled separately by enrich_trades()
        if t.get("status") != "complete":
            skipped_incomplete += 1
            continue
        adds = t.get("adds") or {}
        drops = t.get("drops") or {}
        used_priority = t.get("type") == "waiver"

        for pid, roster_id in adds.items():
            player = players_db.get(str(pid), {})
            roster = rosters_by_id.get(roster_id, {})
            counts_before = roster_position_counts(roster, players_db)
            dropped_pid = next((dpid for dpid, drid in drops.items() if drid == roster_id), None)
            owner_username = roster_names.get(roster_id, {}).get("owner_username")  # internal only, not written out below
            note = OWNER_NOTES.get(owner_username)
            enriched.append({
                "type": t["type"],
                "team_name": roster_names.get(roster_id, {}).get("team_name", "Unknown"),
                "roster_id": roster_id,
                "player_added": resolve_player_name(pid, players_db),
                "player_added_id": pid,
                "position": player.get("position") or ("DEF" if pid in NFL_TEAM_NAMES else None),
                "player_dropped": resolve_player_name(dropped_pid, players_db) if dropped_pid else None,
                "used_waiver_priority": used_priority,
                "waiver_priority_before_move": prev_priority.get(str(roster_id)),
                "priority_cost_description": describe_priority_cost(
                    used_priority, prev_priority.get(str(roster_id)), len(roster_names)
                ),
                "possible_handcuff_of": find_handcuff_teammate(pid, player, roster, players_db),
                "confirmed_homer_transaction": bool(note and note.get("homer_team") == player.get("team")),
            })

    # Chronological per-team summary, built here so the model never has to
    # infer the order of a team's moves (it once told a team's story
    # backwards: "dropped X for Y, then re-added X" when X was added first).
    moves_by_team = {}
    for e in enriched:
        moves_by_team.setdefault(e["roster_id"], []).append(e)
    for roster_id, moves in moves_by_team.items():
        parts = []
        for n, m in enumerate(moves, 1):
            piece = f"{n}) added {m['player_added']} ({m['position'] or 'unknown position'})"
            if m["player_dropped"]:
                piece += f", dropped {m['player_dropped']}"
            parts.append(piece)
        summary = ("In chronological order (oldest first), this team's moves this week were: "
                   + "; ".join(parts) + ".")
        added_names = {m["player_added"] for m in moves}
        for n, m in enumerate(moves, 1):
            m["move_number_for_team"] = n
            m["team_total_moves"] = len(moves)
            m["team_move_sequence"] = summary
            # flag only when the player dropped here was added EARLIER this week
            earlier_adds = {x["player_added"] for x in moves[:n - 1]}
            m["dropped_player_was_added_earlier_this_week"] = bool(
                m["player_dropped"] and m["player_dropped"] in earlier_adds
            )
    return enriched, skipped_incomplete


def player_summary(pid, players_db):
    """A traded player as the newsletter sees him. age / years_experience
    come straight from Sleeper's own player record and are only quoted if
    present (years_experience 0 means a rookie season)."""
    p = players_db.get(str(pid), {})
    return {
        "name": resolve_player_name(pid, players_db),
        "position": p.get("position") or ("DEF" if str(pid) in NFL_TEAM_NAMES else None),
        "nfl_team": p.get("team"),
        "age": p.get("age"),
        "years_experience": p.get("years_exp"),
    }


def describe_pick(pick, roster_names):
    """Plain-English draft pick. roster_id is the pick's ORIGINAL owner;
    previous_owner_id is who sent it in this trade. Only mention 'originally'
    when those differ (i.e. the pick had already changed hands)."""
    base = f"{pick.get('season')} round {pick.get('round')} pick"
    if pick.get("roster_id") != pick.get("previous_owner_id"):
        original = roster_names.get(pick.get("roster_id"), {}).get("team_name", "another team")
        base += f" (originally {original}'s)"
    return base


def _join(items):
    return ", ".join(items) if items else "nothing"


def enrich_trades(transactions, players_db, roster_names):
    """Completed trades only, with each side's gave/got worked out HERE so
    the model never has to infer who received what. In Sleeper's trade
    records, adds[player] is the roster that RECEIVES him and drops[player]
    is the roster that SENT him; draft_picks[i].owner_id receives the pick
    and previous_owner_id sends it. Same rule as every other fix in this
    pipeline: hand the model the finished conclusion (plain_summary)."""
    trades = []
    ordered = sorted(transactions, key=lambda t: t.get("created") or t.get("status_updated") or 0)
    for t in ordered:
        if t.get("type") != "trade" or t.get("status") != "complete":
            continue
        adds = t.get("adds") or {}
        drops = t.get("drops") or {}
        picks = t.get("draft_picks") or []
        roster_ids = set(t.get("roster_ids") or []) | set(adds.values()) | set(drops.values())
        roster_ids |= {p.get("owner_id") for p in picks} | {p.get("previous_owner_id") for p in picks}
        roster_ids.discard(None)

        sides = []
        for rid in sorted(roster_ids):
            team = roster_names.get(rid, {}).get("team_name", "Unknown")
            gets_players = [player_summary(pid, players_db) for pid, r in adds.items() if r == rid]
            gives_players = [player_summary(pid, players_db) for pid, r in drops.items() if r == rid]
            gets_picks = [describe_pick(p, roster_names) for p in picks if p.get("owner_id") == rid]
            gives_picks = [describe_pick(p, roster_names) for p in picks if p.get("previous_owner_id") == rid]
            gets = [f"{p['name']} ({p['position']})" for p in gets_players] + gets_picks
            gives = [f"{p['name']} ({p['position']})" for p in gives_players] + gives_picks
            owner_username = roster_names.get(rid, {}).get("owner_username")  # internal only
            note = OWNER_NOTES.get(owner_username)
            sides.append({
                "team_name": team,
                "receives_players": gets_players,
                "receives_picks": gets_picks,
                "sends_players": gives_players,
                "sends_picks": gives_picks,
                "plain_summary": f"{team} gave up {_join(gives)} and received {_join(gets)}.",
                "confirmed_homer_acquisition": bool(
                    note and note.get("homer_team")
                    and any(p["nfl_team"] == note["homer_team"] for p in gets_players)
                ),
            })
        trades.append({
            "teams_involved": [sd["team_name"] for sd in sides],
            "sides": sides,
            "plain_summary": " ".join(sd["plain_summary"] for sd in sides),
        })
    return trades


def track_waiver_priority(story_state, rosters):
    """Snapshot each roster's current waiver_position so next week's run
    can tell whether a team burned a good priority slot on this week's
    claim. Rolling waivers reset/reorder after every claim, so this only
    means anything as a week-over-week diff, never a single-week value."""
    current = {str(r["roster_id"]): r.get("settings", {}).get("waiver_position")
               for r in rosters}
    story_state["last_known_waiver_priority"] = current


def update_transaction_tracking(story_state, new_transactions, week):
    """Add this week's adds to the rolling tracker so future weeks can
    report real cumulative points scored since the pickup.

    Deduplicated by (roster_id, player_added_id, week_added) — confirmed in
    production that re-running the workflow multiple times for the same
    week (routine during testing/debugging) silently appended a fresh
    duplicate entry every single time, with no cleanup. One case grew to
    105 tracked entries where 22 distinct ones should have existed. This
    function must be safe to call any number of times for the same week's
    data without changing the result beyond the first call.

    priority_cost_description and waiver_priority_at_add are carried over
    from the enriched transaction at creation time and persist for as long
    as this entry is tracked — confirmed in production this context was
    previously LOST the moment an add left transactions_this_week: a real
    week-1 add tracked into week 2 lost all trace of how cheap or costly
    the original claim actually was, so a genuinely free (priority #12 of
    12) pickup got described the same as a premium one. Computed once, at
    the time of the add, since that's the only point where the "before"
    priority number is meaningful — it should never be recomputed later."""
    tracked = story_state.setdefault("transaction_tracking", [])
    existing_keys = {(t["roster_id"], t["player_added_id"], t["week_added"]) for t in tracked}
    for tx in new_transactions:
        if tx["type"] == "trade":
            continue  # trades tracked separately if desired later; keep v1 simple
        key = (tx["roster_id"], tx["player_added_id"], week)
        if key in existing_keys:
            continue  # already tracked from a prior run of this same week — don't duplicate
        tracked.append({
            "week_added": week,
            "roster_id": tx["roster_id"],
            "team_name": tx["team_name"],
            "player_added": tx["player_added"],
            "player_added_id": tx["player_added_id"],
            "used_waiver_priority": tx["used_waiver_priority"],
            "waiver_priority_at_add": tx.get("waiver_priority_before_move"),
            "priority_cost_description": tx.get("priority_cost_description"),
            "cumulative_points_since_add": 0.0,
            "weeks_tracked": 0,
            "weeks_counted": [],  # which week numbers have already had points applied — prevents double-counting on re-runs
        })
        existing_keys.add(key)
    return tracked


def refresh_tracked_points(story_state, current_week_matchups, current_week):
    """For every add still inside its tracking window, add this week's
    actual scored points (already under the league's real scoring) to its
    running total. Drops tracking after TRANSACTION_TRACKING_WINDOW_WEEKS.

    Idempotent per week via weeks_counted — re-running this for the same
    current_week (routine during testing) must not add the same week's
    points to the total a second time. Older entries from before this
    field existed default to an empty list via .setdefault, so they'll
    correctly accept the next real week's points without re-adding
    anything already (incorrectly) baked into their total."""
    tracked = story_state.get("transaction_tracking", [])
    points_by_roster = {m["roster_id"]: m.get("players_points", {}) for m in current_week_matchups}
    still_tracking = []
    for entry in tracked:
        entry.setdefault("weeks_counted", [])
        weeks_elapsed = current_week - entry["week_added"]
        if 0 <= weeks_elapsed <= TRANSACTION_TRACKING_WINDOW_WEEKS:
            if current_week not in entry["weeks_counted"]:
                pts = points_by_roster.get(entry["roster_id"], {}).get(str(entry["player_added_id"]), 0) or 0
                entry["cumulative_points_since_add"] += pts
                entry["weeks_tracked"] += 1
                entry["weeks_counted"].append(current_week)
            still_tracking.append(entry)
        elif weeks_elapsed < 0:
            still_tracking.append(entry)  # not reached yet, shouldn't happen but keep safe
    story_state["transaction_tracking"] = still_tracking
    return still_tracking


def main():
    nfl_state = get_nfl_state()
    week_to_recap, week_to_preview = determine_weeks(nfl_state)

    rosters = get_rosters()
    rosters_by_id = {r["roster_id"]: r for r in rosters}
    users = get_users()
    roster_names = roster_id_to_team_info(rosters, users)
    players_db = get_players_cached()
    league = get_league()
    roster_positions = league.get("roster_positions") or []
    print(f"League lineup slots: {[p for p in roster_positions if p != 'BN']} "
          f"(bench {roster_positions.count('BN')}), playoff_teams setting: "
          f"{(league.get('settings') or {}).get('playoff_teams')}")

    # Current waiver priority order — deterministic, no LLM involved. This
    # is a plain factual list; there's no upside to having the model
    # author it and real risk of it getting an order or name wrong.
    current_waiver_priority_order = sorted(
        (
            {
                "priority": r.get("settings", {}).get("waiver_position"),
                "team_name": roster_names.get(r["roster_id"], {}).get("team_name", "Unknown"),
            }
            for r in rosters
        ),
        key=lambda x: (x["priority"] is None, x["priority"]),
    )

    matchups_recap = get_matchups(week_to_recap)
    matchups_preview = get_matchups(week_to_preview)
    raw_transactions = get_transactions(week_to_recap)
    standings = build_standings(rosters, roster_names)

    try:
        with open(PATHS["story_state"]) as f:
            story_state = json.load(f)
    except FileNotFoundError:
        story_state = {}

    enriched_tx, skipped_incomplete = enrich_transactions(raw_transactions, players_db, roster_names, rosters_by_id, story_state)
    trades_this_week = enrich_trades(raw_transactions, players_db, roster_names)
    update_transaction_tracking(story_state, enriched_tx, week_to_recap)
    tracked_results = refresh_tracked_points(story_state, matchups_recap, week_to_recap)  # needs raw players_points — must run BEFORE enrich_matchup_players
    track_waiver_priority(story_state, rosters)  # snapshot AFTER this week's claims for next week's diff

    def annotate(matchups):
        for m in matchups:
            m["team_name"] = roster_names.get(m["roster_id"], {}).get("team_name", "Unknown")
        return matchups

    annotate(matchups_recap)
    compute_matchup_results(matchups_recap)  # adds result (W/L/T), opponent_team_name, opponent_points, margin
    enrich_matchup_players(matchups_recap, players_db, roster_positions, rosters_by_id)  # adds named players_resolved/top_scorer_on_roster/bench_mistake
    bench_mistake_streaks = update_bench_mistake_streaks(story_state, matchups_recap, week_to_recap)  # needs bench_mistake — must run AFTER enrich_matchup_players
    league_top_scorer = compute_league_top_scorer(matchups_recap)
    strip_raw_ids(matchups_recap)  # drop id-only fields now that everything that reads them has run
    strip_raw_ids(matchups_preview)
    annotate(matchups_preview)  # preview has no scores yet — names not useful there, skip enrichment
    attach_opponent_names(matchups_preview)  # who's playing whom next week — see docstring for why this can't be left to inference

    # Single write, now that everything that touches story_state (including
    # bench_mistake_streaks, which needs matchup enrichment to have already
    # run) is done. Previously this wrote too early and bench_mistake_streaks
    # couldn't exist yet at that point.
    with open(PATHS["story_state"], "w") as f:
        json.dump(story_state, f, indent=2)

    data = {
        "season": nfl_state["season"],
        "week_recapped": week_to_recap,
        "week_upcoming": week_to_preview,
        "standings": standings,
        "playoff_picture": compute_playoff_picture(standings),
        "current_waiver_priority_order": current_waiver_priority_order,
        "matchups_recap": matchups_recap,
        "matchups_preview": matchups_preview,
        "league_top_scorer": league_top_scorer,
        "bench_mistake_streaks": bench_mistake_streaks,
        "transactions_this_week": enriched_tx,
        "trades_this_week": trades_this_week,
        "transaction_tracking_all_active": tracked_results,
    }

    with open(PATHS["raw_data"], "w") as f:
        json.dump(data, f, indent=2)

    print(f"Fetched week {week_to_recap} recap + week {week_to_preview} preview. "
          f"{len(enriched_tx)} completed transactions ({skipped_incomplete} failed/incomplete claims filtered out), "
          f"{len(trades_this_week)} completed trade(s), "
          f"{len(tracked_results)} adds under active tracking.")


if __name__ == "__main__":
    main()
