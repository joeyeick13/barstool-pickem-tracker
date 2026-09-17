from __future__ import annotations

from common import (
    PICKS_FILE,
    load_json,
    now_iso,
    save_json,
)


# ============================================================
# VERIFIED DATA CORRECTIONS
#
# Purpose:
#   Apply ONLY corrections that have been manually verified
#   against official Barstool Pick Em source cards/posts.
#
# Important:
#   - Idempotent: safe to run repeatedly.
#   - Does not grade wagers.
#   - Does not overwrite official PAT HILL result rows.
#   - Does not use expected total pick counts as a deletion rule.
#   - Later legitimate "adds" are allowed to increase weekly counts.
# ============================================================


def text(value):
    return str(value or "").strip()


def normalized(value):
    return " ".join(
        text(value)
        .lower()
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
        .split()
    )


def picker_is(pick, name):
    return normalized(
        pick.get("picker")
    ) == normalized(name)


def pick_week(pick):
    try:
        return int(
            pick.get("week") or 0
        )
    except Exception:
        return 0


def is_official(pick):
    return bool(
        pick.get("official_reconciled")
    )


def selection_is(pick, value):
    return (
        normalized(
            pick.get("selection")
        )
        ==
        normalized(value)
    )


def event_is(pick, event_id):
    return (
        str(
            pick.get("event_id") or ""
        )
        ==
        str(event_id)
    )


def set_if_different(
    pick,
    key,
    value,
):
    if pick.get(key) == value:
        return False

    pick[key] = value
    return True


def clear_schedule_match(pick):
    """
    Clear derived ESPN scheduling fields after changing
    matchup/team context.

    event_id is intentionally removed when the correction changes
    the identity/context of the wager so schedule_enrich.py can
    safely resolve it again.
    """

    changed = False

    for key in (
        "event_id",
        "game_time",
        "game_matchup",
        "game_match_status",
        "game_match_source",
        "game_match_confidence",
        "grading_error",
        "result",
        "status",
        "final_score",
        "profit",
        "graded_at",
        "period_score",
        "grade_source",
    ):
        if key in pick:
            pick.pop(key, None)
            changed = True

    return changed


def mark_verified(
    pick,
    reason,
):
    changed = False

    changed |= set_if_different(
        pick,
        "verified_correction",
        True,
    )

    changed |= set_if_different(
        pick,
        "verified_correction_reason",
        reason,
    )

    # Timestamp only when the correction metadata is newly changed.
    if changed:
        pick[
            "verified_correction_at"
        ] = now_iso()

    return changed


# ============================================================
# WEEK 2 VERIFIED CORRECTIONS
# ============================================================

def fix_week2_rico_army_duplicate(picks):
    """
    Rico Week 2 Army -3.5 appeared more than once historically.

    Keep the earliest legitimate row and remove later duplicates.
    """

    matches = [
        pick
        for pick in picks
        if (
            not is_official(pick)
            and pick_week(pick) == 2
            and picker_is(
                pick,
                "Rico Bosco",
            )
            and selection_is(
                pick,
                "Army -3.5",
            )
        )
    ]

    if len(matches) <= 1:
        print(
            "RICO DUPLICATE CHECK: "
            "Army -3.5 already appears once"
        )
        return 0

    matches.sort(
        key=lambda pick: (
            text(
                pick.get("posted_at")
            ),
            text(
                pick.get("source_post_id")
            ),
        )
    )

    keep = matches[0]

    remove_ids = {
        id(pick)
        for pick in matches[1:]
    }

    picks[:] = [
        pick
        for pick in picks
        if id(pick) not in remove_ids
    ]

    print(
        "RICO DUPLICATE REMOVED:",
        "Army -3.5",
        "| kept source:",
        keep.get("source_post_id"),
    )

    return len(remove_ids)


def fix_week2_uconn(
    picks,
):
    """
    Verified Week 2 source-card corrections:

      Dave:
        UConn +12.5

      Big Cat:
        UConn +12.5

    ESPN event:
      401858444
    """

    changed = 0

    targets = (
        (
            "Stool Presidente",
            {
                "UConn +2.5",
                "UConn +12.5",
            },
            "DAVE VERIFIED: UConn +12.5",
        ),
        (
            "Big Cat",
            {
                "Maryland +12.5",
                "UConn +12.5",
            },
            "BIG CAT UCONN VERIFIED: UConn +12.5",
        ),
    )

    for (
        picker,
        possible_selections,
        log_message,
    ) in targets:

        candidate = None

        for pick in picks:
            if (
                is_official(pick)
                or pick_week(pick) != 2
                or not picker_is(
                    pick,
                    picker,
                )
            ):
                continue

            if normalized(
                pick.get("selection")
            ) in {
                normalized(value)
                for value in possible_selections
            }:
                candidate = pick
                break

        if candidate is None:
            print(
                "WARNING — VERIFIED WEEK 2 "
                "UCONN PICK NOT FOUND:",
                picker,
            )
            continue

        row_changed = False

        row_changed |= set_if_different(
            candidate,
            "selection",
            "UConn +12.5",
        )

        row_changed |= set_if_different(
            candidate,
            "team",
            "UConn",
        )

        row_changed |= set_if_different(
            candidate,
            "opponent",
            "Maryland",
        )

        row_changed |= set_if_different(
            candidate,
            "matchup",
            "UConn @ Maryland",
        )

        row_changed |= set_if_different(
            candidate,
            "bet_type",
            "SPREAD",
        )

        row_changed |= set_if_different(
            candidate,
            "side",
            "UConn",
        )

        row_changed |= set_if_different(
            candidate,
            "line",
            12.5,
        )

        row_changed |= set_if_different(
            candidate,
            "event_id",
            "401858444",
        )

        if row_changed:
            mark_verified(
                candidate,
                "Verified Week 2 UConn +12.5 source-card correction",
            )
            changed += 1

        print(log_message)

    return changed


def fix_week2_big_cat_southern_total(
    picks,
):
    """
    Verified Big Cat Week 2:
      Southern @ Houston Over 60.5

    ESPN event:
      401856787
    """

    candidate = None

    for pick in picks:
        if (
            is_official(pick)
            or pick_week(pick) != 2
            or not picker_is(
                pick,
                "Big Cat",
            )
        ):
            continue

        if selection_is(
            pick,
            "Over 60.5",
        ) and (
            event_is(
                pick,
                "401856787",
            )
            or "southern" in normalized(
                pick.get("matchup")
            )
            or "houston" in normalized(
                pick.get("matchup")
            )
        ):
            candidate = pick
            break

    if candidate is None:
        print(
            "WARNING — BIG CAT VERIFIED "
            "SOUTHERN/HOUSTON TOTAL NOT FOUND"
        )
        return 0

    changed = False

    changed |= set_if_different(
        candidate,
        "selection",
        "Over 60.5",
    )

    changed |= set_if_different(
        candidate,
        "matchup",
        "Southern @ Houston",
    )

    changed |= set_if_different(
        candidate,
        "bet_type",
        "TOTAL",
    )

    changed |= set_if_different(
        candidate,
        "side",
        "OVER",
    )

    changed |= set_if_different(
        candidate,
        "line",
        60.5,
    )

    changed |= set_if_different(
        candidate,
        "event_id",
        "401856787",
    )

    if changed:
        mark_verified(
            candidate,
            "Verified Week 2 Southern @ Houston Over 60.5",
        )

    print(
        "BIG CAT TOTAL VERIFIED: "
        "Southern @ Houston Over 60.5 "
        "| event 401856787"
    )

    return int(changed)


# ============================================================
# WEEK 3 INITIAL CARD
#
# Verified from the three official cards supplied:
#
# Big Cat: 10 initial picks
# Rico:    11 initial picks
# Dave:    12 initial picks
#
# Total:   33 initial wagers
#
# This does NOT restrict later Week 3 adds.
# ============================================================

WEEK3_INITIAL = {
    "Big Cat": [
        {
            "selection": "Under 51.5",
            "matchup": "Syracuse @ Pittsburgh",
            "bet_type": "TOTAL",
            "side": "UNDER",
            "line": 51.5,
        },
        {
            "selection": "Under 53.5",
            "matchup": "Houston @ Texas Tech",
            "bet_type": "TOTAL",
            "side": "UNDER",
            "line": 53.5,
        },
        {
            "selection": "Arizona State -5.5",
            "matchup": "Arizona State @ Kansas",
            "team": "Arizona State",
            "opponent": "Kansas",
            "bet_type": "SPREAD",
            "side": "Arizona State",
            "line": -5.5,
        },
        {
            "selection": "Over 53.5",
            "matchup": "Georgia @ Arkansas",
            "bet_type": "TOTAL",
            "side": "OVER",
            "line": 53.5,
        },
        {
            "selection": "Georgia -24.5",
            "aliases": [
                "Georgia -24.5",
                "UGA -24.5",
            ],
            "matchup": "Georgia @ Arkansas",
            "team": "Georgia",
            "opponent": "Arkansas",
            "bet_type": "SPREAD",
            "side": "Georgia",
            "line": -24.5,
        },
        {
            "selection": "Over 59.5",
            "matchup": "SMU @ Louisville",
            "bet_type": "TOTAL",
            "side": "OVER",
            "line": 59.5,
        },
        {
            "selection": "Florida -2.5",
            "matchup": "Florida @ Auburn",
            "team": "Florida",
            "opponent": "Auburn",
            "bet_type": "SPREAD",
            "side": "Florida",
            "line": -2.5,
        },
        {
            "selection": "LSU -3",
            "matchup": "LSU vs Ole Miss",
            "team": "LSU",
            "opponent": "Ole Miss",
            "bet_type": "SPREAD",
            "side": "LSU",
            "line": -3.0,
        },
        {
            "selection": "Virginia Tech -3",
            "matchup": "Virginia Tech @ Maryland",
            "team": "Virginia Tech",
            "opponent": "Maryland",
            "bet_type": "SPREAD",
            "side": "Virginia Tech",
            "line": -3.0,
        },
        {
            "selection": "New Mexico +22.5",
            "matchup": "New Mexico @ Oklahoma",
            "team": "New Mexico",
            "opponent": "Oklahoma",
            "bet_type": "SPREAD",
            "side": "New Mexico",
            "line": 22.5,
        },
    ],

    "Rico Bosco": [
        {
            "selection": "Under 53.5",
            "matchup": "Houston @ Texas Tech",
            "bet_type": "TOTAL",
            "side": "UNDER",
            "line": 53.5,
        },
        {
            "selection": "Arizona State -5.5",
            "matchup": "Arizona State @ Kansas",
            "team": "Arizona State",
            "opponent": "Kansas",
            "bet_type": "SPREAD",
            "side": "Arizona State",
            "line": -5.5,
        },
        {
            "selection": "Over 50.5",
            "matchup": "Arizona State @ Kansas",
            "bet_type": "TOTAL",
            "side": "OVER",
            "line": 50.5,
        },
        {
            "selection": "North Carolina +3.5",
            "matchup": "North Carolina @ Clemson",
            "team": "North Carolina",
            "opponent": "Clemson",
            "bet_type": "SPREAD",
            "side": "North Carolina",
            "line": 3.5,
        },
        {
            "selection": "Minnesota -24.5",
            "matchup": "Akron @ Minnesota",
            "team": "Minnesota",
            "opponent": "Akron",
            "bet_type": "SPREAD",
            "side": "Minnesota",
            "line": -24.5,
        },
        {
            "selection": "Louisville -1.5",
            "matchup": "SMU @ Louisville",
            "team": "Louisville",
            "opponent": "SMU",
            "bet_type": "SPREAD",
            "side": "Louisville",
            "line": -1.5,
        },
        {
            "selection": "Over 49.5",
            "matchup": "Kentucky @ Texas A&M",
            "bet_type": "TOTAL",
            "side": "OVER",
            "line": 49.5,
        },
        {
            "selection": "Under 56.5",
            "matchup": "Utah State @ Utah",
            "bet_type": "TOTAL",
            "side": "UNDER",
            "line": 56.5,
        },
        {
            "selection": "Under 53.5",
            "matchup": "Florida @ Auburn",
            "bet_type": "TOTAL",
            "side": "UNDER",
            "line": 53.5,
        },
        {
            "selection": "Over 59.5",
            "matchup": "LSU vs Ole Miss",
            "bet_type": "TOTAL",
            "side": "OVER",
            "line": 59.5,
        },
        {
            "selection": "Arizona -34.5",
            "matchup": "Northern Illinois @ Arizona",
            "team": "Arizona",
            "opponent": "Northern Illinois",
            "bet_type": "SPREAD",
            "side": "Arizona",
            "line": -34.5,
        },
    ],

    "Stool Presidente": [
        {
            "selection": "Wake Forest +20",
            "matchup": "Miami @ Wake Forest",
            "team": "Wake Forest",
            "opponent": "Miami",
            "bet_type": "SPREAD",
            "side": "Wake Forest",
            "line": 20.0,
        },
        {
            "selection": "Houston +7.5",
            "matchup": "Houston @ Texas Tech",
            "team": "Houston",
            "opponent": "Texas Tech",
            "bet_type": "SPREAD",
            "side": "Houston",
            "line": 7.5,
        },
        {
            "selection": "Kansas +5.5",
            "matchup": "Arizona State @ Kansas",
            "team": "Kansas",
            "opponent": "Arizona State",
            "bet_type": "SPREAD",
            "side": "Kansas",
            "line": 5.5,
        },
        {
            "selection": "Boston College -37.5",
            "matchup": "Maine @ Boston College",
            "team": "Boston College",
            "opponent": "Maine",
            "bet_type": "SPREAD",
            "side": "Boston College",
            "line": -37.5,
        },
        {
            "selection": "Louisville -1.5",
            "matchup": "SMU @ Louisville",
            "team": "Louisville",
            "opponent": "SMU",
            "bet_type": "SPREAD",
            "side": "Louisville",
            "line": -1.5,
        },
        {
            "selection": "Over 59.5",
            "matchup": "SMU @ Louisville",
            "bet_type": "TOTAL",
            "side": "OVER",
            "line": 59.5,
        },
        {
            "selection": "South Carolina -4",
            "matchup": "Mississippi State @ South Carolina",
            "team": "South Carolina",
            "opponent": "Mississippi State",
            "bet_type": "SPREAD",
            "side": "South Carolina",
            "line": -4.0,
        },
        {
            "selection": "Over 58.5",
            "aliases": [
                "Over 58.5",
                "Miss St/UofSC o58.5",
            ],
            "matchup": "Mississippi State @ South Carolina",
            "bet_type": "TOTAL",
            "side": "OVER",
            "line": 58.5,
        },
        {
            "selection": "Auburn +2.5",
            "matchup": "Florida @ Auburn",
            "team": "Auburn",
            "opponent": "Florida",
            "bet_type": "SPREAD",
            "side": "Auburn",
            "line": 2.5,
        },
        {
            "selection": "Ole Miss +3",
            "matchup": "LSU vs Ole Miss",
            "team": "Ole Miss",
            "opponent": "LSU",
            "bet_type": "SPREAD",
            "side": "Ole Miss",
            "line": 3.0,
        },
        {
            "selection": "Maryland +3.5",
            "matchup": "Virginia Tech @ Maryland",
            "team": "Maryland",
            "opponent": "Virginia Tech",
            "bet_type": "SPREAD",
            "side": "Maryland",
            "line": 3.5,
        },
        {
            "selection": "Purdue +14",
            "matchup": "Purdue @ UCLA",
            "team": "Purdue",
            "opponent": "UCLA",
            "bet_type": "SPREAD",
            "side": "Purdue",
            "line": 14.0,
        },
    ],
}


def rule_aliases(rule):
    values = list(
        rule.get("aliases")
        or []
    )

    values.append(
        rule["selection"]
    )

    return {
        normalized(value)
        for value in values
    }


def week3_candidates(
    picks,
    picker,
    rule,
):
    aliases = rule_aliases(
        rule
    )

    return [
        pick
        for pick in picks
        if (
            not is_official(pick)
            and pick_week(pick) == 3
            and picker_is(
                pick,
                picker,
            )
            and normalized(
                pick.get("selection")
            )
            in aliases
        )
    ]


def matchup_matches_rule(
    pick,
    rule,
):
    stored = normalized(
        pick.get("matchup")
    )

    expected = normalized(
        rule.get("matchup")
    )

    if not stored:
        return False

    if stored == expected:
        return True

    # The exact formatting may differ, but if every meaningful
    # word from the expected matchup exists in the stored matchup,
    # treat it as the same source-card wager.
    expected_words = {
        word
        for word in expected.split()
        if word not in {
            "at",
            "vs",
        }
    }

    stored_words = set(
        stored.split()
    )

    return bool(
        expected_words
        and expected_words.issubset(
            stored_words
        )
    )


def choose_week3_row(
    candidates,
    rule,
    already_used,
):
    available = [
        pick
        for pick in candidates
        if id(pick)
        not in already_used
    ]

    if not available:
        return None

    # Prefer a row whose existing matchup already identifies
    # this exact source-card wager.
    matchup_matches = [
        pick
        for pick in available
        if matchup_matches_rule(
            pick,
            rule,
        )
    ]

    if len(matchup_matches) == 1:
        return matchup_matches[0]

    if matchup_matches:
        matchup_matches.sort(
            key=lambda pick: (
                text(
                    pick.get("posted_at")
                ),
                text(
                    pick.get(
                        "source_post_id"
                    )
                ),
            )
        )
        return matchup_matches[0]

    # If there is only one remaining candidate for this exact
    # selection, it is safe to use it.
    if len(available) == 1:
        return available[0]

    # Multiple same-selection rows can be legitimate totals.
    # We assign in source order according to the verified card
    # order rather than deleting them.
    available.sort(
        key=lambda pick: (
            text(
                pick.get("posted_at")
            ),
            text(
                pick.get(
                    "source_post_id"
                )
            ),
        )
    )

    return available[0]


def apply_week3_rule(
    pick,
    rule,
):
    changed = False

    # Determine whether matchup/team identity is changing.
    old_matchup = normalized(
        pick.get("matchup")
    )

    new_matchup = normalized(
        rule.get("matchup")
    )

    identity_changed = (
        bool(new_matchup)
        and old_matchup
        and old_matchup != new_matchup
    )

    changed |= set_if_different(
        pick,
        "selection",
        rule["selection"],
    )

    changed |= set_if_different(
        pick,
        "matchup",
        rule["matchup"],
    )

    changed |= set_if_different(
        pick,
        "bet_type",
        rule["bet_type"],
    )

    changed |= set_if_different(
        pick,
        "side",
        rule["side"],
    )

    changed |= set_if_different(
        pick,
        "line",
        rule["line"],
    )

    if "team" in rule:
        changed |= set_if_different(
            pick,
            "team",
            rule["team"],
        )

    if "opponent" in rule:
        changed |= set_if_different(
            pick,
            "opponent",
            rule["opponent"],
        )

    if identity_changed:
        changed |= clear_schedule_match(
            pick
        )

    if changed:
        mark_verified(
            pick,
            (
                "Verified against official "
                "Week 3 initial Pick Em card"
            ),
        )

    return changed


def reconcile_week3_initial_cards(
    picks,
):
    """
    Reconcile ONLY the verified initial-card wagers.

    This function intentionally does not delete arbitrary Week 3 rows.
    It only removes duplicate rows representing the same verified
    initial-card wager.

    Legitimate later added picks remain untouched.
    """

    changed_rows = 0
    removed_rows = 0

    used = set()
    verified_rows = {}

    print()
    print(
        "========== WEEK 3 INITIAL CARD RECONCILIATION =========="
    )

    for picker, rules in WEEK3_INITIAL.items():

        verified_rows[picker] = []

        for rule in rules:

            candidates = week3_candidates(
                picks,
                picker,
                rule,
            )

            selected = choose_week3_row(
                candidates,
                rule,
                used,
            )

            if selected is None:
                print(
                    "WARNING — WEEK 3 INITIAL PICK NOT FOUND:",
                    picker,
                    "|",
                    rule["selection"],
                    "|",
                    rule["matchup"],
                )
                continue

            used.add(
                id(selected)
            )

            verified_rows[
                picker
            ].append(
                selected
            )

            if apply_week3_rule(
                selected,
                rule,
            ):
                changed_rows += 1
                print(
                    "WEEK 3 VERIFIED/CORRECTED:",
                    picker,
                    "|",
                    rule["selection"],
                    "|",
                    rule["matchup"],
                )
            else:
                print(
                    "WEEK 3 ALREADY VERIFIED:",
                    picker,
                    "|",
                    rule["selection"],
                    "|",
                    rule["matchup"],
                )

    # --------------------------------------------------------
    # Remove ONLY two proven duplicate patterns:
    #
    # Big Cat:
    #   Georgia -24.5 / UGA -24.5
    #
    # Dave:
    #   Over 58.5 / Miss St/UofSC o58.5
    #
    # Keep exactly the verified selected row for each.
    # --------------------------------------------------------

    duplicate_specs = [
        {
            "picker":
                "Big Cat",

            "aliases": {
                normalized(
                    "Georgia -24.5"
                ),
                normalized(
                    "UGA -24.5"
                ),
            },

            "matchup":
                "Georgia @ Arkansas",
        },

        {
            "picker":
                "Stool Presidente",

            "aliases": {
                normalized(
                    "Over 58.5"
                ),
                normalized(
                    "Miss St/UofSC o58.5"
                ),
            },

            "matchup":
                "Mississippi State @ South Carolina",
        },
    ]

    remove_ids = set()

    for spec in duplicate_specs:

        picker = spec[
            "picker"
        ]

        possible = [
            pick
            for pick in picks
            if (
                not is_official(pick)
                and pick_week(pick) == 3
                and picker_is(
                    pick,
                    picker,
                )
                and normalized(
                    pick.get(
                        "selection"
                    )
                )
                in spec["aliases"]
            )
        ]

        keep = None

        for pick in verified_rows.get(
            picker,
            [],
        ):
            if (
                normalized(
                    pick.get(
                        "selection"
                    )
                )
                in spec["aliases"]
                and normalized(
                    pick.get(
                        "matchup"
                    )
                )
                ==
                normalized(
                    spec["matchup"]
                )
            ):
                keep = pick
                break

        if keep is None:
            print(
                "WARNING — DUPLICATE GROUP "
                "HAS NO VERIFIED KEEP ROW:",
                picker,
                "|",
                spec["matchup"],
            )
            continue

        for pick in possible:
            if pick is keep:
                continue

            remove_ids.add(
                id(pick)
            )

            print(
                "WEEK 3 PROVEN DUPLICATE REMOVED:",
                picker,
                "|",
                pick.get(
                    "selection"
                ),
                "| source:",
                pick.get(
                    "source_post_id"
                ),
            )

    if remove_ids:

        removed_rows = len(
            remove_ids
        )

        picks[:] = [
            pick
            for pick in picks
            if id(pick)
            not in remove_ids
        ]

    # --------------------------------------------------------
    # Audit the verified initial-card presence.
    #
    # Counts here refer ONLY to verified initial card wagers,
    # not all Week 3 wagers. Later official adds are allowed.
    # --------------------------------------------------------

    print()
    print(
        "WEEK 3 VERIFIED INITIAL CARD AUDIT"
    )

    expected = {
        "Big Cat": 10,
        "Rico Bosco": 11,
        "Stool Presidente": 12,
    }

    all_good = True

    for picker, expected_count in expected.items():

        actual = len(
            verified_rows.get(
                picker,
                []
            )
        )

        print(
            picker,
            "| verified initial picks:",
            actual,
            "/",
            expected_count,
        )

        if actual != expected_count:
            all_good = False

    if all_good:
        print(
            "WEEK 3 INITIAL CARD AUDIT PASSED: "
            "33 / 33 verified wagers"
        )
    else:
        print(
            "WARNING — WEEK 3 INITIAL CARD AUDIT "
            "DID NOT REACH 33 / 33"
        )

    print(
        "Week 3 rows corrected:",
        changed_rows,
    )

    print(
        "Week 3 proven duplicates removed:",
        removed_rows,
    )

    print(
        "========================================================="
    )

    return (
        changed_rows,
        removed_rows,
    )


# ============================================================
# WEEK 2 BIG CAT AUDIT
# ============================================================

def audit_week2_big_cat(
    picks,
):
    """
    The old 34-pick requirement is intentionally removed.

    34 represented Big Cat's card at one point in time.
    Legitimate later official adds increased that number.

    We now verify only the four historically recovered wagers
    that must remain present.
    """

    required = [
        "Grambling/TCU Over 54.5",
        "Prairie View/Baylor Over 55.5",
        "Purdue +3",
        "Oregon State +25.5",
    ]

    big_cat_week2 = [
        pick
        for pick in picks
        if (
            not is_official(pick)
            and pick_week(pick) == 2
            and picker_is(
                pick,
                "Big Cat",
            )
        )
    ]

    present = 0

    for required_selection in required:

        found = any(
            selection_is(
                pick,
                required_selection,
            )
            for pick in big_cat_week2
        )

        if found:
            present += 1
            print(
                "BIG CAT RECOVERED PICK PRESENT:",
                required_selection,
            )
        else:
            print(
                "WARNING — BIG CAT RECOVERED PICK MISSING:",
                required_selection,
            )

    print()
    print(
        "BIG CAT WEEK 2 CURRENT COUNT:",
        len(
            big_cat_week2
        ),
    )

    print(
        "BIG CAT RECOVERED PICKS PRESENT:",
        present,
        "/ 4",
    )

    if present == 4:
        print(
            "BIG CAT RECOVERY AUDIT PASSED"
        )
    else:
        print(
            "WARNING — BIG CAT RECOVERY AUDIT FAILED"
        )


# ============================================================
# MAIN
# ============================================================

def apply_verified_corrections():
    picks = load_json(
        PICKS_FILE,
        []
    )

    if not isinstance(
        picks,
        list,
    ):
        raise RuntimeError(
            "picks.json must contain a list."
        )

    print()
    print(
        "========== VERIFIED DATA CORRECTIONS =========="
    )

    before_count = len(
        picks
    )

    total_changes = 0
    total_removed = 0

    total_removed += (
        fix_week2_rico_army_duplicate(
            picks
        )
    )

    total_changes += (
        fix_week2_uconn(
            picks
        )
    )

    total_changes += (
        fix_week2_big_cat_southern_total(
            picks
        )
    )

    (
        week3_changes,
        week3_removed,
    ) = reconcile_week3_initial_cards(
        picks
    )

    total_changes += (
        week3_changes
    )

    total_removed += (
        week3_removed
    )

    print()
    audit_week2_big_cat(
        picks
    )

    after_count = len(
        picks
    )

    if (
        total_changes
        or total_removed
        or after_count != before_count
    ):
        save_json(
            PICKS_FILE,
            picks,
        )

        print()
        print(
            "Verified correction data saved."
        )

    else:
        print()
        print(
            "No verified corrections needed."
        )

    print(
        "Rows corrected:",
        total_changes,
    )

    print(
        "Rows removed:",
        total_removed,
    )

    print(
        "Total stored wagers after corrections:",
        len(
            picks
        ),
    )

    print(
        "==============================================="
    )

    return picks


if __name__ == "__main__":
    apply_verified_corrections()
