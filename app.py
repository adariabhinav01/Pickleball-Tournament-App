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
        return redirect(url_for("match_entry"))

    return render_template("setup.html", settings=db.get_settings(),
                            already_set_up=db.is_tournament_set_up(),
                            teams=db.get_teams() if db.is_tournament_set_up() else None)


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
