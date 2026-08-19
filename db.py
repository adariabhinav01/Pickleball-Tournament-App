"""
SQLite access layer: connection handling, schema init, standings computation,
schedule/bracket setup, and match submission (with the validation rules
carried over from the original Apps Script).
"""
import sqlite3
from collections import defaultdict
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from flask import g

from scheduler import generate_round_robin, PLAYOFF_STAGES

DB_PATH = Path(__file__).parent / "pickleball.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        _ensure_schema(g.db)
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _ensure_schema(conn):
    """
    Idempotent: runs on every freshly-opened connection (not just once at
    startup), so the app self-heals regardless of how the process was
    launched/restarted (e.g. the Werkzeug debug reloader spinning up a new
    worker process) or whether pickleball.db was deleted and needs recreating.
    """
    conn.executescript(SCHEMA_PATH.read_text())
    if conn.execute("SELECT 1 FROM settings WHERE id = 1").fetchone() is None:
        conn.execute("INSERT INTO settings (id) VALUES (1)")
    conn.commit()


def init_db(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        get_db()  # warms up + ensures schema exists before the first request


# ---------------------------------------------------------------- settings --

def get_settings():
    return dict(get_db().execute("SELECT * FROM settings WHERE id = 1").fetchone())


def update_settings(event_name, location, score_type, match_type):
    db = get_db()
    db.execute(
        "UPDATE settings SET event_name=?, location=?, score_type=?, match_type=? WHERE id=1",
        (event_name, location, score_type, match_type),
    )
    db.commit()


# ------------------------------------------------------------------- teams --

def get_teams():
    return {row["id"]: dict(row) for row in get_db().execute("SELECT * FROM teams ORDER BY id")}


def is_tournament_set_up():
    row = get_db().execute("SELECT COUNT(*) AS n FROM teams").fetchone()
    return row["n"] == 9


def create_teams_and_schedule(teams_data):
    """
    teams_data: list of 9 dicts, in seed order:
        {"name", "player1_name", "player1_dupr_id", "player1_external_id",
         "player2_name", "player2_dupr_id", "player2_external_id"}
    (player*_external_id is DUPR's "ExternalId" -- the player's email.)
    Inserts the teams, generates the round-robin schedule + byes, and creates
    the 7 empty playoff bracket rows. Safe to call only once (teams.id is a
    plain PK, re-running with existing data will raise on the UNIQUE-ish PK
    collision at the DB layer, which is the desired guard against double-setup).
    """
    db = get_db()
    team_ids = []
    for i, t in enumerate(teams_data, start=1):
        db.execute(
            "INSERT INTO teams (id, name, player1_name, player1_dupr_id, player1_external_id, "
            "player2_name, player2_dupr_id, player2_external_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (i, t["name"], t["player1_name"], t["player1_dupr_id"], t.get("player1_external_id", ""),
             t["player2_name"], t["player2_dupr_id"], t.get("player2_external_id", "")),
        )
        team_ids.append(i)

    schedule = generate_round_robin(team_ids)
    for rnd in schedule:
        for match_num, (a, b) in enumerate(rnd["matches"], start=1):
            db.execute(
                "INSERT INTO round_robin_matches (round, match_num, team_a_id, team_b_id) VALUES (?, ?, ?, ?)",
                (rnd["round"], match_num, a, b),
            )
        db.execute("INSERT INTO byes (round, team_id) VALUES (?, ?)", (rnd["round"], rnd["bye"]))

    for i, stage in enumerate(PLAYOFF_STAGES, start=1):
        db.execute(
            "INSERT INTO playoff_matches (id, stage_code, stage_label, feeds_into, feeds_slot) VALUES (?, ?, ?, ?, ?)",
            (i, stage["code"], stage["label"], stage["feeds_into"], stage["feeds_slot"]),
        )
    db.commit()


def prefill_round_robin_scores(scores):
    """
    scores: iterable of (round, match_num, team_a_score, team_b_score, date).
    Applies each through the normal validated submit_rr_score() path (so it's
    held to the exact same scoring rules, logging, snapshotting, and playoff
    auto-seeding as a live submission), keyed to whichever match already
    occupies that round/match_num slot for the schedule that was just
    generated. Used to restore a day's already-played results after a
    database reset instead of forcing them to be re-entered by hand.
    """
    db = get_db()
    for round_num, match_num, a, b, date in scores:
        row = db.execute(
            "SELECT id FROM round_robin_matches WHERE round=? AND match_num=?", (round_num, match_num)
        ).fetchone()
        if row is None:
            continue
        submit_rr_score(row["id"], a, b, date)


# ------------------------------------------------------------ round robin --

def get_round_data(round_num):
    db = get_db()
    matches = db.execute(
        "SELECT * FROM round_robin_matches WHERE round = ? ORDER BY match_num", (round_num,)
    ).fetchall()
    bye = db.execute("SELECT team_id FROM byes WHERE round = ?", (round_num,)).fetchone()
    return [dict(m) for m in matches], (bye["team_id"] if bye else None)


def get_all_rr_matches():
    return [dict(r) for r in get_db().execute("SELECT * FROM round_robin_matches ORDER BY round, match_num")]


def round_is_complete(round_num):
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS n FROM round_robin_matches WHERE round = ? AND (team_a_score IS NULL OR team_b_score IS NULL)",
        (round_num,),
    ).fetchone()
    return row["n"] == 0


SCORE_RULE_MESSAGE = "A game must reach 11 with a win by 2 (e.g. 11-9, 12-10, 15-13)."


def _valid_game_score(a, b):
    """
    Standard pickleball game-win rule: first to 11, win by 2. A win at exactly
    11 requires the loser at 9 or below (11-10 isn't legal -- that's a lead of
    only 1, so play continues); once the score passes 11, the winner's margin
    must be *exactly* 2, since play stops the instant a 2-point lead appears
    -- a winning margin of 3+ (e.g. 14-11, 15-1) is never a real result.
    """
    winner, loser = max(a, b), min(a, b)
    if winner < 11:
        return False
    if winner == 11:
        return loser <= 9
    return winner - loser == 2


def submit_rr_score(match_id, g1_a, g1_b, match_date):
    """Validates and records a round-robin match score. Returns (ok, message)."""
    db = get_db()
    match = db.execute("SELECT * FROM round_robin_matches WHERE id = ?", (match_id,)).fetchone()
    if match is None:
        return False, "Unknown match."
    if g1_a is None or g1_b is None:
        return False, "Game 1 score is required for both teams."
    if not _valid_game_score(g1_a, g1_b):
        return False, SCORE_RULE_MESSAGE

    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE round_robin_matches SET team_a_score=?, team_b_score=?, match_date=?, submitted_at=? WHERE id=?",
        (g1_a, g1_b, match_date, now, match_id),
    )
    _log_submission(now, f"Round {match['round']}", match["team_a_id"], match["team_b_id"],
                     g1_a, g1_b, None, None, None, None, f"round_robin_matches.id={match_id}")
    db.commit()

    if round_is_complete(match["round"]):
        _snapshot_round(match["round"])
    _maybe_seed_playoffs()
    db.commit()
    return True, f"Recorded: Round {match['round']} — {g1_a}-{g1_b}"


def _log_submission(ts, round_or_stage, team_a_id, team_b_id, g1a, g1b, g2a, g2b, g3a, g3b, target):
    get_db().execute(
        "INSERT INTO submission_log (submitted_at, round_or_stage, team_a_id, team_b_id, "
        "g1_a, g1_b, g2_a, g2_b, g3_a, g3_b, target) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts, round_or_stage, team_a_id, team_b_id, g1a, g1b, g2a, g2b, g3a, g3b, target),
    )


# -------------------------------------------------------------- standings --

def compute_standings(matches, team_ids, overrides=None):
    """
    matches: iterable of dicts with team_a_id, team_b_id, team_a_score, team_b_score
             (only completed matches should be passed in).
    Tiebreak chain: Win% -> head-to-head (only resolved for an exact 2-way tie
    on win%) -> point differential. Win% (not raw win count) is primary so
    that, mid-tournament, a team that's played fewer games isn't ranked level
    with one that's played more and has the same number of wins but less
    remaining room to keep winning. Once every team has played the same
    number of games (the round robin is complete), this is equivalent to
    sorting by wins, so it doesn't change anything at the end of the event.
    Win% is compared as an exact fraction (not a float) to avoid rounding
    artifacts when grouping ties.
    """
    stats = {tid: {"team_id": tid, "wins": 0, "losses": 0, "points_for": 0, "points_against": 0} for tid in team_ids}
    h2h_winner = {}  # frozenset({a,b}) -> winning team id

    for m in matches:
        a, b, sa, sb = m["team_a_id"], m["team_b_id"], m["team_a_score"], m["team_b_score"]
        if sa is None or sb is None:
            continue
        stats[a]["points_for"] += sa
        stats[a]["points_against"] += sb
        stats[b]["points_for"] += sb
        stats[b]["points_against"] += sa
        winner, loser = (a, b) if sa > sb else (b, a)
        stats[winner]["wins"] += 1
        stats[loser]["losses"] += 1
        h2h_winner[frozenset((a, b))] = winner

    rows = []
    for s in stats.values():
        games_played = s["wins"] + s["losses"]
        win_pct_frac = Fraction(s["wins"], games_played) if games_played else Fraction(0)
        rows.append({
            **s,
            "games_played": games_played,
            "win_pct": float(win_pct_frac),
            "win_pct_frac": win_pct_frac,
            "point_diff": s["points_for"] - s["points_against"],
        })

    by_pct = defaultdict(list)
    for r in rows:
        by_pct[r["win_pct_frac"]].append(r)
    for group in by_pct.values():
        if len(group) == 2:
            a, b = group
            winner = h2h_winner.get(frozenset((a["team_id"], b["team_id"])))
            a["h2h_adjust"] = 1 if winner == a["team_id"] else (-1 if winner else 0)
            b["h2h_adjust"] = 1 if winner == b["team_id"] else (-1 if winner else 0)
        else:
            for r in group:
                r["h2h_adjust"] = 0

    rows.sort(key=lambda r: (-r["win_pct_frac"], -r["h2h_adjust"], -r["point_diff"]))
    for i, r in enumerate(rows, start=1):
        r["auto_rank"] = i

    overrides = overrides or {}
    for r in rows:
        r["final_rank"] = overrides.get(r["team_id"], r["auto_rank"])
    rows.sort(key=lambda r: r["final_rank"])
    return rows


def get_manual_overrides():
    return {row["team_id"]: row["rank"] for row in get_db().execute("SELECT * FROM manual_overrides")}


def live_standings():
    teams = get_teams()
    matches = [m for m in get_all_rr_matches() if m["team_a_score"] is not None]
    return compute_standings(matches, teams.keys(), get_manual_overrides())


def _snapshot_round(round_num):
    """Freeze standings as of the end of `round_num`, if not already snapshotted."""
    db = get_db()
    existing = db.execute("SELECT 1 FROM standings_snapshots WHERE round = ?", (round_num,)).fetchone()
    if existing:
        return
    teams = get_teams()
    matches = [m for m in get_all_rr_matches() if m["round"] <= round_num and m["team_a_score"] is not None]
    rows = compute_standings(matches, teams.keys())
    for r in rows:
        db.execute(
            "INSERT INTO standings_snapshots (round, team_id, wins, losses, games_played, win_pct, "
            "points_for, points_against, point_diff, rank) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (round_num, r["team_id"], r["wins"], r["losses"], r["games_played"], r["win_pct"],
             r["points_for"], r["points_against"], r["point_diff"], r["auto_rank"]),
        )


def last_round_snapshot():
    db = get_db()
    row = db.execute("SELECT MAX(round) AS r FROM standings_snapshots").fetchone()
    if row["r"] is None:
        return None, []
    rnd = row["r"]
    rows = db.execute(
        "SELECT * FROM standings_snapshots WHERE round = ? ORDER BY rank", (rnd,)
    ).fetchall()
    return rnd, [dict(r) for r in rows]


# --------------------------------------------------------------- playoffs --

def get_playoff_matches():
    return [dict(r) for r in get_db().execute("SELECT * FROM playoff_matches ORDER BY id")]


def get_playoff_match(stage_code):
    row = get_db().execute("SELECT * FROM playoff_matches WHERE stage_code = ?", (stage_code,)).fetchone()
    return dict(row) if row else None


def _maybe_seed_playoffs():
    """Once all 36 round-robin matches are complete, seed QF1-4 from final standings."""
    db = get_db()
    qf1 = get_playoff_match("QF1")
    if qf1["team_a_id"] is not None:
        return  # already seeded
    remaining = db.execute(
        "SELECT COUNT(*) AS n FROM round_robin_matches WHERE team_a_score IS NULL"
    ).fetchone()["n"]
    if remaining > 0:
        return

    standings = live_standings()
    seed_to_team = {r["final_rank"]: r["team_id"] for r in standings}
    for stage in PLAYOFF_STAGES:
        if stage["seed_a"] is None:
            continue
        db.execute(
            "UPDATE playoff_matches SET team_a_id=?, team_b_id=? WHERE stage_code=?",
            (seed_to_team[stage["seed_a"]], seed_to_team[stage["seed_b"]], stage["code"]),
        )


_PLAYOFF_STAGE_RANK = {"Quarterfinalist": 1, "Semifinalist": 2, "Finalist": 3, "Champion": 4}


def playoff_standings_table():
    """
    Returns per-team PLAYOFF-ONLY stats (not round-robin carryover), tracked
    at the individual-game level (a best-of-3 match contributes up to 3 rows
    of game wins/losses, not just 1 match result) -- or None until the first
    playoff game score has actually been entered. Every team starts at 0-0
    the moment this table appears; a team's stats only move once its own
    games are recorded.

    Rather than a seed number, each row carries a "status" -- the furthest
    stage the team has reached, derived by walking QF -> SF -> Final via the
    same feeds_into links used to advance the bracket. A team keeps its
    status once it loses (e.g. loses in the semis -> stays "Semifinalist"
    forever); the Final's winner becomes "Champion".
    """
    matches = get_playoff_matches()
    if not any(m["game1_a"] is not None for m in matches):
        return None

    by_code = {m["stage_code"]: m for m in matches}
    team_ids = []
    for code in ("QF1", "QF2", "QF3", "QF4"):
        team_ids += [by_code[code]["team_a_id"], by_code[code]["team_b_id"]]

    stats = {tid: {"team_id": tid, "games_won": 0, "games_lost": 0, "points_for": 0, "points_against": 0}
              for tid in team_ids}
    for m in matches:
        if m["team_a_id"] is None or m["team_b_id"] is None:
            continue
        ta, tb = m["team_a_id"], m["team_b_id"]
        for a, b in ((m["game1_a"], m["game1_b"]), (m["game2_a"], m["game2_b"]), (m["game3_a"], m["game3_b"])):
            if a is None or b is None:
                continue
            stats[ta]["points_for"] += a
            stats[ta]["points_against"] += b
            stats[tb]["points_for"] += b
            stats[tb]["points_against"] += a
            if a > b:
                stats[ta]["games_won"] += 1
                stats[tb]["games_lost"] += 1
            else:
                stats[tb]["games_won"] += 1
                stats[ta]["games_lost"] += 1

    rows = []
    for tid in team_ids:
        s = stats[tid]
        games_played = s["games_won"] + s["games_lost"]
        status = _playoff_status(tid, by_code)
        rows.append({
            **s,
            "games_played": games_played,
            "win_pct": (s["games_won"] / games_played) if games_played else 0.0,
            "point_diff": s["points_for"] - s["points_against"],
            "status": status,
        })
    rows.sort(key=lambda r: (-_PLAYOFF_STAGE_RANK[r["status"]], -r["win_pct"], -r["point_diff"]))
    return rows


def _playoff_status(team_id, by_code):
    qf = next(by_code[c] for c in ("QF1", "QF2", "QF3", "QF4") if team_id in (by_code[c]["team_a_id"], by_code[c]["team_b_id"]))
    if qf["winner_team_id"] != team_id:
        return "Quarterfinalist"
    sf = by_code[qf["feeds_into"]]
    if sf["winner_team_id"] != team_id:
        return "Semifinalist"
    final = by_code["Final"]
    if final["winner_team_id"] == team_id:
        return "Champion"
    return "Finalist"


def tournament_champion():
    """Returns the winning team's dict once the Final has a decided winner, else None."""
    final = get_playoff_match("Final")
    if final is None or final["winner_team_id"] is None:
        return None
    return get_teams()[final["winner_team_id"]]


def consume_confetti_flag():
    """
    Returns True exactly once -- the first time this is called after the
    Final has a decided winner -- then flips a persisted flag so every later
    call (including a fresh page load) returns False. That's what makes the
    championship confetti a one-time celebration instead of something that
    replays every time the Playoffs page is visited.
    """
    db = get_db()
    final = get_playoff_match("Final")
    if final is None or final["winner_team_id"] is None or final["confetti_shown"]:
        return False
    db.execute("UPDATE playoff_matches SET confetti_shown=1 WHERE stage_code='Final'")
    db.commit()
    return True


def submit_playoff_score(stage_code, g1a, g1b, g2a, g2b, g3a, g3b, match_date):
    db = get_db()
    match = get_playoff_match(stage_code)
    if match is None:
        return False, "Unknown bracket stage."
    if match["team_a_id"] is None or match["team_b_id"] is None:
        return False, "That bracket slot isn't determined yet — record the earlier feeder match(es) first."
    if g1a is None or g1b is None:
        return False, "Game 1 score is required."
    for a, b in ((g1a, g1b), (g2a, g2b), (g3a, g3b)):
        if a is None and b is None:
            continue
        if a is None or b is None:
            return False, "Both teams' scores are needed for any game that's been started."
        if not _valid_game_score(a, b):
            return False, SCORE_RULE_MESSAGE

    games_a = sum(1 for a, b in ((g1a, g1b), (g2a, g2b), (g3a, g3b)) if a is not None and a > b)
    games_b = sum(1 for a, b in ((g1a, g1b), (g2a, g2b), (g3a, g3b)) if a is not None and a < b)
    winner_id = None
    if games_a >= 2:
        winner_id = match["team_a_id"]
    elif games_b >= 2:
        winner_id = match["team_b_id"]

    now = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE playoff_matches SET game1_a=?, game1_b=?, game2_a=?, game2_b=?, game3_a=?, game3_b=?, "
        "match_date=?, winner_team_id=?, submitted_at=? WHERE stage_code=?",
        (g1a, g1b, g2a, g2b, g3a, g3b, match_date, winner_id, now, stage_code),
    )
    _log_submission(now, stage_code, match["team_a_id"], match["team_b_id"],
                     g1a, g1b, g2a, g2b, g3a, g3b, f"playoff_matches.stage_code={stage_code}")

    if winner_id is not None and match["feeds_into"]:
        col = "team_a_id" if match["feeds_slot"] == "a" else "team_b_id"
        db.execute(f"UPDATE playoff_matches SET {col}=? WHERE stage_code=?", (winner_id, match["feeds_into"]))

    db.commit()
    msg = f"Recorded: {stage_code}"
    if winner_id is not None:
        winner_name = get_teams()[winner_id]["name"]
        msg += f" — {winner_name} wins ({games_a}-{games_b})"
    else:
        msg += " (not yet decided — needs more games)"
    return True, msg


# ------------------------------------------------------------ DUPR export --

def export_rows():
    """
    Returns list of dicts, one per completed match, in the DUPR Import column
    layout (matching DUPR Import!A1:AA1 from the original workbook, minus the
    ReadyToExport helper column). Round-robin rows first (round/match_num
    order), then playoff rows (QF1-4, SF1-2, Final).
    """
    settings = get_settings()
    teams = get_teams()
    rows = []

    def player_cols(team_id, slot):
        t = teams[team_id]
        return {
            f"player{slot}1": t["player1_name"], f"player{slot}1DuprId": t["player1_dupr_id"],
            f"player{slot}1ExternalId": t["player1_external_id"],
            f"player{slot}2": t["player2_name"], f"player{slot}2DuprId": t["player2_dupr_id"],
            f"player{slot}2ExternalId": t["player2_external_id"],
        }

    def base_row(date):
        return {
            "matchType": settings["match_type"],
            "event": settings["event_name"],
            "date": date or "",
            "location": settings["location"],
            "scoreType": settings["score_type"],
        }

    for m in get_all_rr_matches():
        if m["team_a_score"] is None or m["team_b_score"] is None:
            continue
        row = base_row(m["match_date"])
        row.update(player_cols(m["team_a_id"], "A"))
        row.update(player_cols(m["team_b_id"], "B"))
        row.update({
            "teamAGame1": m["team_a_score"], "teamBGame1": m["team_b_score"],
            "teamAGame2": "", "teamBGame2": "", "teamAGame3": "", "teamBGame3": "",
            "teamAGame4": "", "teamBGame4": "", "teamAGame5": "", "teamBGame5": "",
        })
        rows.append(row)

    for m in get_playoff_matches():
        if m["winner_team_id"] is None:  # only actually-decided best-of-3 matches
            continue
        row = base_row(m["match_date"])
        row.update(player_cols(m["team_a_id"], "A"))
        row.update(player_cols(m["team_b_id"], "B"))
        games = [(m["game1_a"], m["game1_b"]), (m["game2_a"], m["game2_b"]), (m["game3_a"], m["game3_b"])]
        for i, (a, b) in enumerate(games, start=1):
            row[f"teamAGame{i}"] = a if a is not None else ""
            row[f"teamBGame{i}"] = b if b is not None else ""
        row["teamAGame4"] = row["teamBGame4"] = row["teamAGame5"] = row["teamBGame5"] = ""
        rows.append(row)

    return rows


DUPR_COLUMNS = [
    "matchType", "event", "date",
    "playerA1", "playerA1DuprId", "playerA1ExternalId",
    "playerA2", "playerA2DuprId", "playerA2ExternalId",
    "playerB1", "playerB1DuprId", "playerB1ExternalId",
    "playerB2", "playerB2DuprId", "playerB2ExternalId",
    "teamAGame1", "teamBGame1", "teamAGame2", "teamBGame2", "teamAGame3", "teamBGame3",
    "teamAGame4", "teamBGame4", "teamAGame5", "teamBGame5",
    "location", "scoreType",
]
