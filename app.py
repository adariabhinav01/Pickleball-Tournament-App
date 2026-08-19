import csv
import io
import math

from flask import Flask, render_template, request, redirect, url_for, Response

import db

app = Flask(__name__)
db.init_db(app)


def _generate_confetti(n=28):
    """Pieces spread in a burst pattern around the Final's match-card, with a
    slight upward bias and a small color palette cycling through them."""
    colors = ["#ff595e", "#ffca3a", "#8ac926", "#1982c4", "#6a4c93"]
    pieces = []
    for i in range(n):
        angle = (2 * math.pi * i / n) + (0.12 if i % 2 else -0.08)
        radius = 65 + (i % 4) * 22
        pieces.append({
            "tx": round(radius * math.cos(angle)),
            "ty": round(radius * math.sin(angle) - 25),
            "color": colors[i % len(colors)],
            "delay": round((i % 9) * 0.03, 2),
        })
    return pieces


CONFETTI_PIECES = _generate_confetti()

# Real roster for this event, pulled from the original workbook's Teams/Members
# sheets (the Teams sheet's own DuprId/email lookup formulas were never
# recalculated in Excel, so these were resolved by matching names against
# Members instead). Used only to pre-fill the one-time Setup form.
# player*_external_id is DUPR's "ExternalId" field, which is each player's email.
PREFILL_TEAMS = [
    {"name": "Team 1", "player1_name": "Sivanaga Chatakonda", "player1_dupr_id": "DD76PL",
     "player1_external_id": "cshiva83@gmail.com",
     "player2_name": "Santhosh Kolluri", "player2_dupr_id": "NNP2RN",
     "player2_external_id": "skolluri812@gmail.com"},
    {"name": "Team 2", "player1_name": "Kelvin Silakhom", "player1_dupr_id": "G7J96M",
     "player1_external_id": "ksilakhom@gmail.com",
     "player2_name": "Murthy Adari", "player2_dupr_id": "Q92WM4",
     "player2_external_id": "avnmurthy@gmail.com"},
    {"name": "Team 3", "player1_name": "Santosh Ayala", "player1_dupr_id": "G7JYWX",
     "player1_external_id": "santosha.a@gmail.com",
     "player2_name": "Naveen Bandi", "player2_dupr_id": "MZOYXO",
     "player2_external_id": "naveen.bandi86@gmail.com"},
    {"name": "Team 4", "player1_name": "Srikanth Thirunahari", "player1_dupr_id": "DD7ZL6",
     "player1_external_id": "srik.thirunahari@gmail.com",
     "player2_name": "Ravi Choppalli", "player2_dupr_id": "KZNJ4O",
     "player2_external_id": "rkchoppalli@gmail.com"},
    {"name": "Team 5", "player1_name": "Bala Bandi", "player1_dupr_id": "57N2MR",
     "player1_external_id": "balaswamy.bandi@gmail.com",
     "player2_name": "Anil Mudigonda", "player2_dupr_id": "PPX2WM",
     "player2_external_id": "anilpraveen@yahoo.com"},
    {"name": "Team 6", "player1_name": "Ravi Kiran Diwakarla", "player1_dupr_id": "DD0MRY",
     "player1_external_id": "ravikiran.diwakarla@gmail.com",
     "player2_name": "Vivekan Shanmugam", "player2_dupr_id": "MZMXQL",
     "player2_external_id": "s.vivekan@gmail.com"},
    {"name": "Team 7", "player1_name": "Lakshmi Malagaveli", "player1_dupr_id": "VL0VE7",
     "player1_external_id": "reddy.ml@gmail.com",
     "player2_name": "Srini Nampelly", "player2_dupr_id": "6PQMRG",
     "player2_external_id": "nampelly.srinivas@gmail.com"},
    {"name": "Team 8", "player1_name": "RamPrasad Alla", "player1_dupr_id": "P5O0YZ",
     "player1_external_id": "ramprasad.alla@gmail.com",
     "player2_name": "Vijaya Kumar Rani", "player2_dupr_id": "7K7DM5",
     "player2_external_id": "rvkumar@gmail.com"},
    {"name": "Team 9", "player1_name": "Venugopal Ambadipudi", "player1_dupr_id": "WKZ4XK",
     "player1_external_id": "gopalav@gmail.com",
     "player2_name": "Rajesh Kolluru", "player2_dupr_id": "2D4KY9",
     "player2_external_id": "rajeshk.kolluru@gmail.com"},
]

# Round robin was played and fully scored on 8/15; baked in here (same idea
# as PREFILL_TEAMS) so a database reset -- e.g. after a future schema change
# -- doesn't lose that day's real results and force re-entering all 36
# matches by hand. Applied automatically right after teams are created.
# (round, match_num, team_a_score, team_b_score, date)
PREFILL_RR_SCORES = [
    (1, 1, 9, 11, "2026-08-15"), (1, 2, 9, 11, "2026-08-15"), (1, 3, 5, 11, "2026-08-15"), (1, 4, 8, 11, "2026-08-15"),
    (2, 1, 5, 11, "2026-08-15"), (2, 2, 9, 11, "2026-08-15"), (2, 3, 7, 11, "2026-08-15"), (2, 4, 9, 11, "2026-08-15"),
    (3, 1, 11, 9, "2026-08-15"), (3, 2, 11, 1, "2026-08-15"), (3, 3, 4, 11, "2026-08-15"), (3, 4, 4, 11, "2026-08-15"),
    (4, 1, 6, 11, "2026-08-15"), (4, 2, 8, 11, "2026-08-15"), (4, 3, 11, 7, "2026-08-15"), (4, 4, 6, 11, "2026-08-15"),
    (5, 1, 10, 12, "2026-08-15"), (5, 2, 11, 5, "2026-08-15"), (5, 3, 7, 11, "2026-08-15"), (5, 4, 11, 7, "2026-08-15"),
    (6, 1, 4, 11, "2026-08-15"), (6, 2, 5, 11, "2026-08-15"), (6, 3, 11, 7, "2026-08-15"), (6, 4, 10, 12, "2026-08-15"),
    (7, 1, 9, 11, "2026-08-15"), (7, 2, 2, 11, "2026-08-15"), (7, 3, 11, 7, "2026-08-15"), (7, 4, 2, 11, "2026-08-15"),
    (8, 1, 3, 11, "2026-08-15"), (8, 2, 11, 8, "2026-08-15"), (8, 3, 7, 11, "2026-08-15"), (8, 4, 11, 4, "2026-08-15"),
    (9, 1, 5, 11, "2026-08-15"), (9, 2, 11, 9, "2026-08-15"), (9, 3, 8, 11, "2026-08-15"), (9, 4, 11, 1, "2026-08-15"),
]

# Round robin was played on 8/15, playoffs are on 8/16 -- pre-fill each
# screen's date field accordingly so the operator doesn't have to set it
# match after match.
DEFAULT_RR_DATE = "2026-08-15"
DEFAULT_PLAYOFF_DATE = "2026-08-16"
app.jinja_env.globals["DEFAULT_RR_DATE"] = DEFAULT_RR_DATE
app.jinja_env.globals["DEFAULT_PLAYOFF_DATE"] = DEFAULT_PLAYOFF_DATE


class InvalidScore(ValueError):
    pass


def _parse_score(raw):
    """
    Parses a score field. Blank -> None (not entered yet). Anything that isn't
    a plain whole number (no sign, no decimal point, no exponent, no letters)
    raises InvalidScore instead of letting int() throw an unhandled exception
    that would otherwise 500 the request.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise InvalidScore(f"'{raw}' isn't a valid score")
    return int(raw)


# --------------------------------------------------------------------- home --

@app.route("/")
def index():
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    return redirect(url_for("match_entry"))


# -------------------------------------------------------------------- setup --

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        if db.is_tournament_set_up():
            return render_template("setup.html", error="Teams are already set up.",
                                    settings=db.get_settings(), already_set_up=True)
        teams_data = []
        for i in range(1, 10):
            teams_data.append({
                "name": request.form.get(f"team{i}_name", f"Team {i}").strip(),
                "player1_name": request.form.get(f"team{i}_p1_name", "").strip(),
                "player1_dupr_id": request.form.get(f"team{i}_p1_dupr", "").strip(),
                "player1_external_id": request.form.get(f"team{i}_p1_email", "").strip(),
                "player2_name": request.form.get(f"team{i}_p2_name", "").strip(),
                "player2_dupr_id": request.form.get(f"team{i}_p2_dupr", "").strip(),
                "player2_external_id": request.form.get(f"team{i}_p2_email", "").strip(),
            })
        db.create_teams_and_schedule(teams_data)
        db.prefill_round_robin_scores(PREFILL_RR_SCORES)
        return redirect(url_for("match_entry"))

    return render_template("setup.html", settings=db.get_settings(),
                            already_set_up=db.is_tournament_set_up(),
                            teams=db.get_teams() if db.is_tournament_set_up() else None,
                            prefill=PREFILL_TEAMS)


@app.route("/setup/settings", methods=["POST"])
def update_settings():
    db.update_settings(
        request.form.get("event_name", "").strip(),
        request.form.get("location", "").strip(),
        request.form.get("score_type", "").strip(),
        request.form.get("match_type", "").strip(),
    )
    return redirect(url_for("setup"))


# ------------------------------------------------------------- match entry --

@app.route("/match-entry")
def match_entry():
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    if "round" in request.args:
        round_num = int(request.args["round"])
    else:
        # No round explicitly requested (e.g. clicked "Match Entry" from another
        # page) -> land on the leftmost round that isn't fully scored yet,
        # rather than always resetting to Round 1.
        round_num = next((r for r in range(1, 10) if not db.round_is_complete(r)), 9)
    return render_template("match_entry.html", round_num=round_num, teams=db.get_teams(),
                            **_round_panel_context(round_num))


def _round_panel_context(round_num):
    matches, bye_team_id = db.get_round_data(round_num)
    return {
        "matches": matches,
        "bye_team_id": bye_team_id,
        "round_complete": db.round_is_complete(round_num),
        "completed_rounds": {r for r in range(1, 10) if db.round_is_complete(r)},
        "message": None,
    }


@app.route("/match-entry/rr/<int:match_id>", methods=["POST"])
def submit_rr(match_id):
    round_num = int(request.form.get("round_num"))
    ctx = _round_panel_context(round_num)
    try:
        g1_a = _parse_score(request.form.get("g1_a"))
        g1_b = _parse_score(request.form.get("g1_b"))
    except InvalidScore:
        ctx["message"] = "Scores must be whole numbers (0 or greater)."
        ctx["ok"] = False
        return render_template("_round_panel.html", round_num=round_num, teams=db.get_teams(), **ctx)

    ok, message = db.submit_rr_score(match_id, g1_a, g1_b, request.form.get("match_date", "").strip())
    ctx = _round_panel_context(round_num)
    ctx["message"] = message
    ctx["ok"] = ok
    return render_template("_round_panel.html", round_num=round_num, teams=db.get_teams(), **ctx)


# ------------------------------------------------------------------ standings --

@app.route("/standings")
def standings():
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    return render_template("standings.html", **_standings_context())


@app.route("/standings/partial")
def standings_partial():
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    return render_template("_standings_tables.html", **_standings_context())


def _standings_context():
    teams = db.get_teams()
    live = db.live_standings()
    snap_round, snap_rows = db.last_round_snapshot()
    return {
        "teams": teams, "live": live, "snap_round": snap_round, "snap_rows": snap_rows,
        "playoff_standings": db.playoff_standings_table(),
    }


# ------------------------------------------------------------------- playoffs --

@app.route("/playoffs")
def playoffs():
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    return render_template("playoffs.html", **_playoffs_context())


def _playoffs_context(message=None, ok=None):
    champion = db.tournament_champion()
    show_confetti = db.consume_confetti_flag() if champion is not None else False
    return {
        "matches": db.get_playoff_matches(), "teams": db.get_teams(), "message": message, "ok": ok,
        "champion": champion, "show_confetti": show_confetti, "confetti": CONFETTI_PIECES,
    }


@app.route("/playoffs/<stage_code>", methods=["POST"])
def submit_playoff(stage_code):
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    try:
        scores = [
            _parse_score(request.form.get(f)) for f in ("g1_a", "g1_b", "g2_a", "g2_b", "g3_a", "g3_b")
        ]
    except InvalidScore:
        return render_template("_bracket.html", **_playoffs_context("Scores must be whole numbers (0 or greater).", False))

    ok, message = db.submit_playoff_score(stage_code, *scores, request.form.get("match_date", "").strip())
    return render_template("_bracket.html", **_playoffs_context(message, ok))


# --------------------------------------------------------------------- export --

@app.route("/export")
def export_page():
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    rows = db.export_rows()
    return render_template("export.html", row_count=len(rows), rows=rows, columns=db.DUPR_COLUMNS)


@app.route("/export/dupr.csv", methods=["POST"])
def export_csv():
    if not db.is_tournament_set_up():
        return redirect(url_for("setup"))
    rows = db.export_rows()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=db.DUPR_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=dupr_import.csv"},
    )


if __name__ == "__main__":
    app.run(debug=True)
