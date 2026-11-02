"""
Shared constants for the newsletter pipeline.
Nothing sensitive lives here — this file is committed to a public repo.
Secrets (API keys) come from GitHub Actions env vars only, never this file.
"""

LEAGUE_NAME = "Only Offensive Matters"
LEAGUE_ID = "1312159330107146240"

# owner_id -> username, copied from the dynasty war room app's CONFIG.OWNER_MAP.
# Used only as an internal key (OWNER_NOTES matching, fallback label) —
# usernames never appear in the newsletter copy.
OWNER_MAP = {
    "699738649817878528": "bnesmithhicks",
    "699646352681959424": "Saturn75",
    "634248191616888832": "joecressaty",
    "731683896370008064": "Noah742",
    "603294071460532224": "wazimo",
    "1133214675782643712": "BrianHixon",
    "1133216109961969664": "derekangl15",
    "559519052479676416": "NateDawg726",
    "739715132984307712": "demon44",
    "997289100904370176": "christinanesmith",
    "801223429184409600": "mikelanta",
    "997331216271499264": "Mowens54",
}

MY_OWNER_ID = "634248191616888832"

# Not confirmed for this league. Left unset on purpose so the newsletter
# never labels anyone commissioner by guess; set to the commissioner's
# username (a value from OWNER_MAP above) once known.
COMMISSIONER_OWNER_USERNAME = None

# Confirmed owner-psychology notes to feed the newsletter's voice.
# "homer_team" (optional) is an NFL team abbreviation — if set, any add or
# trade acquisition on that team for this owner gets deterministically
# flagged as a confirmed tendency hit, instead of relying on the model to
# notice it.
OWNER_NOTES = {
    "wazimo": {
        "note": "Real, repeatable KC homer — reaches on Chiefs players in any format.",
        "homer_team": "KC",
    },
}

# Sleeper's player database doesn't populate full_name for team defenses
# the same way it does individual players (confirmed in production: DEF
# adds/drops were resolving to "Player DET" instead of "Detroit Lions").
# The reliable fallback is this static mapping.
NFL_TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def resolve_player_name(player_id, players_db):
    """Look up a player's real name, with a guaranteed-correct fallback for
    team defenses via NFL_TEAM_NAMES rather than a generic 'Player {id}'
    placeholder."""
    player = players_db.get(str(player_id), {})
    name = player.get("full_name")
    if name:
        return name
    if player_id in NFL_TEAM_NAMES:
        return NFL_TEAM_NAMES[player_id]
    return f"Player {player_id}"

PATHS = {
    "raw_data": "newsletter/state/raw_week_data.json",
    "story_state": "newsletter/state/story_state.json",
    "newsletter_draft": "newsletter/state/newsletter_draft.json",
    "meme": "newsletter/state/meme.json",
    "template": "newsletter/templates/newsletter_template.html",
    "output_dir": "newsletter/output",
}

# Claude model used for both newsletter copy and meme selection.
CLAUDE_MODEL = "claude-sonnet-5"
