-- Chandler Friends Pickleball Tournament — schema
-- One-off for a 9-team round robin -> top-8 single-elim bracket event.

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
    event_name TEXT NOT NULL DEFAULT 'Tri-City Paddles 2026 Q3 King of the Ring',
    location TEXT NOT NULL DEFAULT 'Frontier Family Park',
    score_type TEXT NOT NULL DEFAULT 'SIDEOUT',
    match_type TEXT NOT NULL DEFAULT 'D'  -- D = Doubles
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,           -- 1..9, matches Team# in the original workbook
    name TEXT NOT NULL,
    player1_name TEXT NOT NULL,
    player1_dupr_id TEXT NOT NULL DEFAULT '',
    player1_external_id TEXT NOT NULL DEFAULT '',  -- DUPR "ExternalId" = player's email
    player2_name TEXT NOT NULL,
    player2_dupr_id TEXT NOT NULL DEFAULT '',
    player2_external_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS round_robin_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round INTEGER NOT NULL,           -- 1..9
    match_num INTEGER NOT NULL,       -- 1..4 within the round
    team_a_id INTEGER NOT NULL REFERENCES teams(id),
    team_b_id INTEGER NOT NULL REFERENCES teams(id),
    team_a_score INTEGER,
    team_b_score INTEGER,
    match_date TEXT,
    submitted_at TEXT
);

CREATE TABLE IF NOT EXISTS byes (
    round INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id)
);

CREATE TABLE IF NOT EXISTS playoff_matches (
    id INTEGER PRIMARY KEY,           -- fixed 1..7
    stage_code TEXT NOT NULL UNIQUE,  -- QF1, QF2, QF3, QF4, SF1, SF2, Final
    stage_label TEXT NOT NULL,        -- "Quarterfinal 1", etc, for display
    team_a_id INTEGER REFERENCES teams(id),   -- null until seeding/feeder resolves
    team_b_id INTEGER REFERENCES teams(id),
    feeds_into TEXT,                  -- stage_code this match's winner advances to, null for Final
    feeds_slot TEXT,                  -- 'a' or 'b' -- which slot in feeds_into this winner fills
    game1_a INTEGER, game1_b INTEGER,
    game2_a INTEGER, game2_b INTEGER,
    game3_a INTEGER, game3_b INTEGER,
    match_date TEXT,
    winner_team_id INTEGER REFERENCES teams(id),
    submitted_at TEXT,
    confetti_shown INTEGER NOT NULL DEFAULT 0  -- Final only: has the champion confetti already played once?
);

CREATE TABLE IF NOT EXISTS standings_snapshots (
    round INTEGER NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams(id),
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    games_played INTEGER NOT NULL,
    win_pct REAL NOT NULL,
    points_for INTEGER NOT NULL,
    points_against INTEGER NOT NULL,
    point_diff INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    PRIMARY KEY (round, team_id)
);

CREATE TABLE IF NOT EXISTS manual_overrides (
    team_id INTEGER PRIMARY KEY REFERENCES teams(id),
    rank INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS submission_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_at TEXT NOT NULL,
    round_or_stage TEXT NOT NULL,
    team_a_id INTEGER,
    team_b_id INTEGER,
    g1_a INTEGER, g1_b INTEGER,
    g2_a INTEGER, g2_b INTEGER,
    g3_a INTEGER, g3_b INTEGER,
    target TEXT NOT NULL
);
