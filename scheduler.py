"""
Round-robin schedule generation and playoff bracket seeding.

Uses the standard "circle method" for round-robin scheduling: fix one team,
rotate the rest around it each round. For an odd number of teams (9, here),
a phantom "bye" slot is added to make the count even (10), and whichever
real team lands opposite the phantom slot in a given round sits that round
out.

Verified against the original workbook: with teams numbered 1-9 and the
phantom slot appended last, Round 1 comes out as 2v9, 3v8, 4v7, 5v6, bye=1,
which matches Chandler_Friends_Pickleball_Tournament.xlsx exactly.
"""

BYE = None


def generate_round_robin(team_ids):
    """
    team_ids: list of 9 team ids, in seed/entry order (1..9).
    Returns: list of 9 rounds, each a dict:
        {"round": r, "matches": [(team_a, team_b), ...], "bye": team_id}
    """
    n = len(team_ids)
    arr = list(team_ids)
    if n % 2 == 1:
        arr.append(BYE)
    total = len(arr)
    num_rounds = total - 1
    half = total // 2

    fixed = arr[0]
    rotating = arr[1:]

    schedule = []
    for r in range(num_rounds):
        current = [fixed] + rotating
        matches = []
        bye_team = None
        for i in range(half):
            a = current[i]
            b = current[total - 1 - i]
            if a is BYE:
                bye_team = b
            elif b is BYE:
                bye_team = a
            else:
                matches.append((a, b))
        schedule.append({"round": r + 1, "matches": matches, "bye": bye_team})
        # rotate right: last element of `rotating` moves to the front
        rotating = [rotating[-1]] + rotating[:-1]

    return schedule


# Fixed single-elimination bracket shape for a top-8 field.
# Matchups keep the best seeds apart until as late as possible: 1v8, 2v7, 3v6,
# 4v5; seed 1's side of the bracket also holds seed 4 (and seed 2's side
# holds seed 3), so QF1+QF4 feed the same semifinal and QF2+QF3 feed the
# other. QF1/QF2/QF3/QF4 are numbered (and displayed, top to bottom) so that
# each *adjacent* pair (QF1&QF2, QF3&QF4) is the pair that faces off in the
# same semifinal -- that's what makes the bracket visually read correctly as
# a bracket, rather than QF1 pairing with the non-adjacent QF4.
PLAYOFF_STAGES = [
    {"code": "QF1", "label": "Quarterfinal 1", "seed_a": 1, "seed_b": 8, "feeds_into": "SF1", "feeds_slot": "a"},
    {"code": "QF2", "label": "Quarterfinal 2", "seed_a": 4, "seed_b": 5, "feeds_into": "SF1", "feeds_slot": "b"},
    {"code": "QF3", "label": "Quarterfinal 3", "seed_a": 2, "seed_b": 7, "feeds_into": "SF2", "feeds_slot": "a"},
    {"code": "QF4", "label": "Quarterfinal 4", "seed_a": 3, "seed_b": 6, "feeds_into": "SF2", "feeds_slot": "b"},
    {"code": "SF1", "label": "Semifinal 1", "seed_a": None, "seed_b": None, "feeds_into": "Final", "feeds_slot": "a"},
    {"code": "SF2", "label": "Semifinal 2", "seed_a": None, "seed_b": None, "feeds_into": "Final", "feeds_slot": "b"},
    {"code": "Final", "label": "Final", "seed_a": None, "seed_b": None, "feeds_into": None, "feeds_slot": None},
]
