from __future__ import annotations

from collections import Counter, defaultdict

from common import PICKS_FILE, STATE_FILE, load_json

from football_identity import (
    SUPPORTED_MARKETS,
    base_market,
    best_matchup_hints,
    canonical_game_identity,
    canonical_pick_key,
    normalize_bet_type,
    safe_float,
    side_identity,
    spread_line_from_selection,
    total_direction,
)


TRACKED_PICKERS = {
    "Big Cat",
    "Rico Bosco",
    "Stool Presidente",
}

FINAL_RESULTS = {
    "WIN",
    "LOSS",
    "PUSH",
}

CFB_SPORTS = {
    "CFB",
    "NCAAF",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def picker_name(pick):
    return str(
        pick.get("picker")
        or ""
    ).strip()


def selection_text(pick):
    return str(
        pick.get("selection")
        or ""
    ).strip()


def pick_week(pick):
    try:
        return int(
            pick.get("week")
            or 0
        )
    except Exception:
        return 0


def is_cfb(pick):
    return (
        str(
            pick.get("sport")
            or ""
        ).upper()
        in CFB_SPORTS
    )


def is_official(pick):
    return bool(
        pick.get(
            "official_reconciled"
        )
    )


def result_value(pick):
    return str(
        pick.get("result")
        or ""
    ).upper()


def status_value(pick):
    return str(
        pick.get("status")
        or ""
    ).upper()


def row_label(pick):
    return (
        f"{picker_name(pick) or 'Unknown'}"
        f" | Week {pick_week(pick)}"
        f" | {selection_text(pick) or 'Unknown wager'}"
    )


# ============================================================
# AUDIT COLLECTOR
# ============================================================

class Audit:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []

    def error(
        self,
        code,
        message,
    ):
        self.errors.append(
            (
                code,
                message,
            )
        )

    def warning(
        self,
        code,
        message,
    ):
        self.warnings.append(
            (
                code,
                message,
            )
        )

    def note(
        self,
        code,
        message,
    ):
        self.info.append(
            (
                code,
                message,
            )
        )


# ============================================================
# CORE STRUCTURE
# ============================================================

def audit_basic_structure(
    picks,
    audit,
):
    for index, pick in enumerate(
        picks
    ):
        if not isinstance(
            pick,
            dict,
        ):
            audit.error(
                "ROW_NOT_OBJECT",
                f"Row {index} is not a JSON object.",
            )
            continue

        picker = picker_name(
            pick
        )

        if picker not in TRACKED_PICKERS:
            audit.error(
                "INVALID_PICKER",
                (
                    f"Row {index} has invalid picker: "
                    f"{picker!r}"
                ),
            )

        if not is_cfb(pick):
            audit.warning(
                "NON_CFB_ROW",
                (
                    f"{row_label(pick)} "
                    "is not marked CFB/NCAAF."
                ),
            )

        if pick_week(pick) <= 0:
            audit.error(
                "INVALID_WEEK",
                (
                    f"{row_label(pick)} "
                    "has no valid week."
                ),
            )

        if not selection_text(
            pick
        ):
            audit.error(
                "EMPTY_SELECTION",
                (
                    f"{picker or 'Unknown'} "
                    f"| row {index} has "
                    "an empty selection."
                ),
            )

        bet_type = normalize_bet_type(
            pick.get("bet_type")
        )

        if bet_type not in SUPPORTED_MARKETS:
            audit.error(
                "UNSUPPORTED_MARKET",
                (
                    f"{row_label(pick)} "
                    f"| market={bet_type}"
                ),
            )


# ============================================================
# MARKET INTEGRITY
# ============================================================

def audit_market_integrity(
    picks,
    audit,
):
    for pick in picks:
        if not is_cfb(pick):
            continue

        bet_type = normalize_bet_type(
            pick.get("bet_type")
        )

        if bet_type not in SUPPORTED_MARKETS:
            continue

        market = base_market(
            bet_type
        )

        line = safe_float(
            pick.get("line")
        )

        if market in {
            "SPREAD",
            "TOTAL",
            "TEAM_TOTAL",
        }:
            if line is None:
                audit.error(
                    "MISSING_LINE",
                    (
                        f"{row_label(pick)} "
                        f"| {market} has no "
                        "numeric line."
                    ),
                )

        if market == "SPREAD":
            selected = side_identity(
                pick
            )

            if not selected:
                audit.error(
                    "SPREAD_NO_TEAM",
                    (
                        f"{row_label(pick)} "
                        "has no selected team."
                    ),
                )

            visible_line = (
                spread_line_from_selection(
                    pick
                )
            )

            if (
                visible_line is not None
                and line is not None
                and abs(
                    visible_line - line
                ) > 0.000001
            ):
                audit.error(
                    "SPREAD_SIGN_MISMATCH",
                    (
                        f"{row_label(pick)} "
                        f"| stored line={line} "
                        f"| visible line={visible_line}"
                    ),
                )

        elif market == "TOTAL":
            direction = total_direction(
                pick
            )

            if direction not in {
                "OVER",
                "UNDER",
            }:
                audit.error(
                    "TOTAL_NO_DIRECTION",
                    (
                        f"{row_label(pick)} "
                        "has no OVER/UNDER direction."
                    ),
                )

            game = canonical_game_identity(
                pick
            )

            hints = best_matchup_hints(
                pick
            )

            if (
                not game
                and len(hints) != 2
            ):
                audit.error(
                    "TOTAL_NO_MATCHUP",
                    (
                        f"{row_label(pick)} "
                        "has no complete game identity."
                    ),
                )

        elif market == "TEAM_TOTAL":
            direction = total_direction(
                pick
            )

            if direction not in {
                "OVER",
                "UNDER",
            }:
                audit.error(
                    "TEAM_TOTAL_NO_DIRECTION",
                    (
                        f"{row_label(pick)} "
                        "has no OVER/UNDER direction."
                    ),
                )

            if not side_identity(
                pick
            ):
                audit.error(
                    "TEAM_TOTAL_NO_TEAM",
                    (
                        f"{row_label(pick)} "
                        "has no team identity."
                    ),
                )

        elif market == "MONEYLINE":
            if not side_identity(
                pick
            ):
                audit.error(
                    "MONEYLINE_NO_TEAM",
                    (
                        f"{row_label(pick)} "
                        "has no selected team."
                    ),
                )


# ============================================================
# CANONICAL DUPLICATE AUDIT
# ============================================================

def audit_canonical_duplicates(
    picks,
    audit,
):
    groups = defaultdict(list)

    for index, pick in enumerate(
        picks
    ):
        if not isinstance(
            pick,
            dict,
        ):
            continue

        try:
            key = canonical_pick_key(
                pick
            )
        except Exception as exc:
            audit.error(
                "CANONICAL_KEY_FAILURE",
                (
                    f"{row_label(pick)} "
                    f"| {type(exc).__name__}: "
                    f"{exc}"
                ),
            )
            continue

        groups[key].append(
            (
                index,
                pick,
            )
        )

    for key, members in groups.items():
        if len(members) <= 1:
            continue

        descriptions = [
            (
                f"row {index}: "
                f"{row_label(pick)} "
                f"| matchup="
                f"{pick.get('matchup')}"
                f" | event="
                f"{pick.get('event_id')}"
            )
            for index, pick
            in members
        ]

        audit.error(
            "CANONICAL_DUPLICATE",
            (
                f"{len(members)} rows share "
                f"canonical identity {key!r}: "
                + " || ".join(
                    descriptions
                )
            ),
        )


# ============================================================
# SOURCE-POST DUPLICATE AUDIT
# ============================================================

def audit_source_duplicates(
    picks,
    audit,
):
    groups = defaultdict(list)

    for pick in picks:
        source_id = str(
            pick.get(
                "source_post_id"
            )
            or ""
        )

        if not source_id:
            continue

        if is_official(pick):
            continue

        try:
            key = canonical_pick_key(
                pick
            )
        except Exception:
            continue

        groups[
            (
                source_id,
                key,
            )
        ].append(
            pick
        )

    for (
        source_id,
        key,
    ), rows in groups.items():
        if len(rows) <= 1:
            continue

        audit.error(
            "SOURCE_DUPLICATE",
            (
                f"Source post {source_id} "
                f"contains {len(rows)} stored "
                f"copies of canonical wager "
                f"{key!r}."
            ),
        )


# ============================================================
# EVENT LOCK INTEGRITY
# ============================================================

def audit_event_locks(
    picks,
    audit,
):
    for pick in picks:
        if not is_cfb(pick):
            continue

        if is_official(pick):
            continue

        event_id = str(
            pick.get("event_id")
            or ""
        ).strip()

        result = result_value(
            pick
        )

        status = status_value(
            pick
        )

        # Provisional CFB wagers should eventually have a
        # deterministic ESPN event lock.
        if not event_id:
            audit.error(
                "MISSING_EVENT_LOCK",
                (
                    f"{row_label(pick)} "
                    "has no ESPN event_id."
                ),
            )

        if (
            result in FINAL_RESULTS
            and not event_id
        ):
            audit.error(
                "GRADED_WITHOUT_EVENT",
                (
                    f"{row_label(pick)} "
                    f"is {result} but has no "
                    "ESPN event lock."
                ),
            )

        if (
            status == "FINAL"
            and result
            not in FINAL_RESULTS
        ):
            audit.error(
                "FINAL_WITHOUT_RESULT",
                (
                    f"{row_label(pick)} "
                    f"has status FINAL but "
                    f"result={result!r}."
                ),
            )

        if (
            result in FINAL_RESULTS
            and status != "FINAL"
        ):
            audit.error(
                "RESULT_NOT_FINAL",
                (
                    f"{row_label(pick)} "
                    f"has result {result} "
                    f"but status={status!r}."
                ),
            )


# ============================================================
# EVENT-ID CONSISTENCY
# ============================================================

def audit_event_consistency(
    picks,
    audit,
):
    """
    Detect impossible use of the same ESPN event ID.

    Multiple wagers on one game are expected.

    What is NOT expected is one ESPN event ID being associated
    with multiple contradictory canonical game identities.
    """

    by_event = defaultdict(list)

    for pick in picks:
        if is_official(pick):
            continue

        event_id = str(
            pick.get("event_id")
            or ""
        ).strip()

        if not event_id:
            continue

        by_event[
            event_id
        ].append(
            pick
        )

    for event_id, rows in (
        by_event.items()
    ):
        identities = defaultdict(list)

        for pick in rows:
            game = (
                canonical_game_identity(
                    pick
                )
            )

            if not game:
                hints = (
                    best_matchup_hints(
                        pick
                    )
                )

                if len(hints) == 2:
                    game = tuple(
                        sorted(hints)
                    )

            if not game:
                continue

            identities[
                repr(game)
            ].append(
                pick
            )

        if len(
            identities
        ) <= 1:
            continue

        detail = []

        for identity, identity_rows in (
            identities.items()
        ):
            examples = ", ".join(
                (
                    f"{picker_name(pick)} "
                    f"{selection_text(pick)}"
                )
                for pick
                in identity_rows[:3]
            )

            detail.append(
                f"{identity}: {examples}"
            )

        audit.error(
            "EVENT_MATCHUP_CONFLICT",
            (
                f"ESPN event {event_id} is "
                "attached to contradictory "
                "game identities: "
                + " || ".join(detail)
            ),
        )


# ============================================================
# OFFICIAL PAT HILL INTEGRITY
# ============================================================

def audit_official_rows(
    picks,
    audit,
):
    by_week_picker = defaultdict(
        list
    )

    for pick in picks:
        if not is_official(pick):
            continue

        picker = picker_name(
            pick
        )

        week = pick_week(
            pick
        )

        by_week_picker[
            (
                week,
                picker,
            )
        ].append(
            pick
        )

        result = result_value(
            pick
        )

        status = status_value(
            pick
        )

        if result not in FINAL_RESULTS:
            audit.error(
                "OFFICIAL_INVALID_RESULT",
                (
                    f"{row_label(pick)} "
                    f"| result={result!r}"
                ),
            )

        if status != "FINAL":
            audit.error(
                "OFFICIAL_NOT_FINAL",
                (
                    f"{row_label(pick)} "
                    f"| status={status!r}"
                ),
            )

        if (
            pick.get(
                "official_result_source"
            )
            != "PAT HILL STANDINGS"
        ):
            audit.error(
                "OFFICIAL_BAD_SOURCE",
                (
                    f"{row_label(pick)} "
                    "does not identify "
                    "PAT HILL STANDINGS "
                    "as its official source."
                ),
            )

        if not pick.get(
            "official_result_post_id"
        ):
            audit.error(
                "OFFICIAL_NO_POST",
                (
                    f"{row_label(pick)} "
                    "has no official result "
                    "post ID."
                ),
            )

    # All rows for one official week/picker should come from
    # exactly one authoritative result thread.
    for (
        week,
        picker,
    ), rows in (
        by_week_picker.items()
    ):
        post_ids = {
            str(
                pick.get(
                    "official_result_post_id"
                )
                or ""
            )
            for pick in rows
        }

        post_ids.discard("")

        if len(post_ids) != 1:
            audit.error(
                "OFFICIAL_MULTIPLE_SOURCES",
                (
                    f"Week {week} {picker} "
                    "official rows reference "
                    f"{len(post_ids)} result "
                    f"posts: {sorted(post_ids)}"
                ),
            )


# ============================================================
# MIXED OFFICIAL / PROVISIONAL WEEK AUDIT
# ============================================================

def audit_mixed_week_sources(
    picks,
    audit,
):
    groups = defaultdict(list)

    for pick in picks:
        groups[
            (
                pick_week(pick),
                picker_name(pick),
            )
        ].append(
            pick
        )

    for (
        week,
        picker,
    ), rows in groups.items():
        if (
            week <= 0
            or picker
            not in TRACKED_PICKERS
        ):
            continue

        official_count = sum(
            1
            for pick in rows
            if is_official(pick)
        )

        provisional_count = (
            len(rows)
            - official_count
        )

        if (
            official_count
            and provisional_count
        ):
            audit.error(
                "MIXED_OFFICIAL_PROVISIONAL",
                (
                    f"Week {week} {picker} "
                    f"contains {official_count} "
                    "official rows and "
                    f"{provisional_count} "
                    "provisional rows."
                ),
            )


# ============================================================
# SOURCE INGEST VALIDATION
# ============================================================

def audit_ingest_validation(
    picks,
    audit,
):
    for pick in picks:
        if is_official(pick):
            continue

        source_id = pick.get(
            "source_post_id"
        )

        if not source_id:
            audit.warning(
                "NO_SOURCE_POST",
                (
                    f"{row_label(pick)} "
                    "has no source_post_id."
                ),
            )

        # Historical rows predate validation version 3.
        # They are allowed, but new rows should be stamped.
        if pick.get(
            "ingest_validation_version"
        ) == 3:
            if not pick.get(
                "ingest_validated"
            ):
                audit.error(
                    "INVALID_VALIDATION_STAMP",
                    (
                        f"{row_label(pick)} "
                        "claims ingest validation "
                        "v3 but ingest_validated "
                        "is not true."
                    ),
                )


# ============================================================
# RETRY QUEUE
# ============================================================

def audit_retry_queue(
    state,
    audit,
):
    failed = (
        state.get(
            "failed_post_ids",
            []
        )
        or []
    )

    if not isinstance(
        failed,
        list,
    ):
        audit.error(
            "FAILED_QUEUE_INVALID",
            "failed_post_ids is not a list.",
        )

        return

    if failed:
        audit.warning(
            "FAILED_POSTS_WAITING",
            (
                f"{len(failed)} X post(s) "
                "remain in the retry queue: "
                + ", ".join(
                    str(value)
                    for value
                    in failed[:10]
                )
            ),
        )

    processed = set(
        str(value)
        for value in (
            state.get(
                "processed_post_ids",
                [],
            )
            or []
        )
    )

    failed_set = set(
        str(value)
        for value in failed
    )

    overlap = (
        processed
        & failed_set
    )

    if overlap:
        audit.error(
            "FAILED_MARKED_PROCESSED",
            (
                "These failed X posts are "
                "simultaneously marked processed: "
                + ", ".join(
                    sorted(overlap)
                )
            ),
        )


# ============================================================
# PICK-ID COLLISIONS
# ============================================================

def audit_pick_ids(
    picks,
    audit,
):
    ids = defaultdict(list)

    for index, pick in enumerate(
        picks
    ):
        pick_id = str(
            pick.get("id")
            or ""
        ).strip()

        if not pick_id:
            audit.warning(
                "MISSING_PICK_ID",
                (
                    f"Row {index}: "
                    f"{row_label(pick)} "
                    "has no stored ID."
                ),
            )

            continue

        ids[pick_id].append(
            (
                index,
                pick,
            )
        )

    for pick_id, rows in ids.items():
        if len(rows) <= 1:
            continue

        audit.error(
            "PICK_ID_COLLISION",
            (
                f"Pick ID {pick_id} occurs "
                f"{len(rows)} times: "
                + " || ".join(
                    (
                        f"row {index} "
                        f"{row_label(pick)}"
                    )
                    for index, pick
                    in rows
                )
            ),
        )


# ============================================================
# WEEK COUNTS / OBSERVABILITY
# ============================================================

def audit_week_counts(
    picks,
    audit,
):
    counts = Counter()

    for pick in picks:
        counts[
            (
                pick_week(pick),
                picker_name(pick),
            )
        ] += 1

    weeks = sorted(
        {
            week
            for (
                week,
                _
            ) in counts.keys()
            if week > 0
        }
    )

    for week in weeks:
        parts = []

        for picker in [
            "Rico Bosco",
            "Big Cat",
            "Stool Presidente",
        ]:
            parts.append(
                f"{picker}="
                f"{counts[(week, picker)]}"
            )

        audit.note(
            "WEEK_COUNTS",
            (
                f"Week {week}: "
                + " | ".join(parts)
            ),
        )


# ============================================================
# KNOWN REGRESSION CLASSES
# ============================================================

def audit_regression_classes(
    picks,
    audit,
):
    """
    These are generic regression tests derived from failures
    we have already experienced.

    There are NO Week-3 event IDs or Week-specific matchup
    patches here.
    """

    # --------------------------------------------------------
    # 1. Same picker/week/total line may legitimately occur
    #    on different games.
    #
    #    Make sure canonical identity actually distinguishes
    #    those games.
    # --------------------------------------------------------

    total_groups = defaultdict(
        list
    )

    for pick in picks:
        if is_official(pick):
            continue

        bet_type = normalize_bet_type(
            pick.get("bet_type")
        )

        if base_market(
            bet_type
        ) != "TOTAL":
            continue

        key = (
            picker_name(pick),
            pick_week(pick),
            total_direction(pick),
            safe_float(
                pick.get("line")
            ),
        )

        total_groups[
            key
        ].append(
            pick
        )

    for key, rows in (
        total_groups.items()
    ):
        if len(rows) <= 1:
            continue

        games = set()
        canonical_keys = set()

        for pick in rows:
            game = (
                canonical_game_identity(
                    pick
                )
            )

            if game:
                games.add(
                    repr(game)
                )

            canonical_keys.add(
                repr(
                    canonical_pick_key(
                        pick
                    )
                )
            )

        if (
            len(games) > 1
            and len(canonical_keys)
            < len(games)
        ):
            audit.error(
                "TOTAL_GAME_IDENTITY_COLLISION",
                (
                    "Different games with the "
                    f"same total signature {key!r} "
                    "collapsed to insufficient "
                    "canonical identities."
                ),
            )

    # --------------------------------------------------------
    # 2. Spread identity must include game context whenever
    #    matchup context exists.
    # --------------------------------------------------------

    spread_groups = defaultdict(
        list
    )

    for pick in picks:
        if is_official(pick):
            continue

        bet_type = normalize_bet_type(
            pick.get("bet_type")
        )

        if base_market(
            bet_type
        ) != "SPREAD":
            continue

        key = (
            picker_name(pick),
            pick_week(pick),
            side_identity(pick),
            safe_float(
                pick.get("line")
            ),
        )

        spread_groups[
            key
        ].append(
            pick
        )

    for key, rows in (
        spread_groups.items()
    ):
        if len(rows) <= 1:
            continue

        games = {
            repr(
                canonical_game_identity(
                    pick
                )
            )
            for pick in rows
            if canonical_game_identity(
                pick
            )
        }

        canonical_keys = {
            repr(
                canonical_pick_key(
                    pick
                )
            )
            for pick in rows
        }

        if (
            len(games) > 1
            and len(canonical_keys)
            < len(games)
        ):
            audit.error(
                "SPREAD_GAME_IDENTITY_COLLISION",
                (
                    "Different games with the "
                    f"same spread signature {key!r} "
                    "collapsed to insufficient "
                    "canonical identities."
                ),
            )


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    picks,
    state,
    audit,
):
    print()
    print(
        "=" * 72
    )
    print(
        "TRACKER INTEGRITY AUDIT"
    )
    print(
        "=" * 72
    )

    print(
        "Stored wagers:",
        len(picks),
    )

    official = sum(
        1
        for pick in picks
        if is_official(pick)
    )

    provisional = (
        len(picks)
        - official
    )

    print(
        "Official wagers:",
        official,
    )

    print(
        "Provisional wagers:",
        provisional,
    )

    locked = sum(
        1
        for pick in picks
        if (
            not is_official(pick)
            and pick.get("event_id")
        )
    )

    provisional_cfb = sum(
        1
        for pick in picks
        if (
            not is_official(pick)
            and is_cfb(pick)
        )
    )

    print(
        "Provisional CFB event locks:",
        f"{locked}/{provisional_cfb}",
    )

    failed_queue = (
        state.get(
            "failed_post_ids",
            []
        )
        or []
    )

    print(
        "X retry queue:",
        len(failed_queue),
    )

    print()

    for code, message in audit.info:
        print(
            "INFO:",
            code,
            "|",
            message,
        )

    if audit.warnings:
        print()
        print(
            "-" * 72
        )
        print(
            "WARNINGS"
        )
        print(
            "-" * 72
        )

        for code, message in (
            audit.warnings
        ):
            print(
                "WARNING:",
                code,
                "|",
                message,
            )

    if audit.errors:
        print()
        print(
            "!" * 72
        )
        print(
            "AUDIT FAILURES"
        )
        print(
            "!" * 72
        )

        for code, message in (
            audit.errors
        ):
            print(
                "ERROR:",
                code,
                "|",
                message,
            )

    print()
    print(
        "=" * 72
    )

    print(
        "Errors:",
        len(audit.errors),
    )

    print(
        "Warnings:",
        len(audit.warnings),
    )

    if audit.errors:
        print(
            "TRACKER AUDIT: FAILED"
        )
    else:
        print(
            "TRACKER AUDIT: PASSED"
        )

    print(
        "=" * 72
    )


# ============================================================
# MAIN
# ============================================================

def run_audit():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    state = load_json(
        STATE_FILE,
        {},
    )

    if not isinstance(
        picks,
        list,
    ):
        raise RuntimeError(
            "picks.json must contain a list."
        )

    if not isinstance(
        state,
        dict,
    ):
        raise RuntimeError(
            "state.json must contain an object."
        )

    audit = Audit()

    audit_basic_structure(
        picks,
        audit,
    )

    audit_market_integrity(
        picks,
        audit,
    )

    audit_canonical_duplicates(
        picks,
        audit,
    )

    audit_source_duplicates(
        picks,
        audit,
    )

    audit_event_locks(
        picks,
        audit,
    )

    audit_event_consistency(
        picks,
        audit,
    )

    audit_official_rows(
        picks,
        audit,
    )

    audit_mixed_week_sources(
        picks,
        audit,
    )

    audit_ingest_validation(
        picks,
        audit,
    )

    audit_retry_queue(
        state,
        audit,
    )

    audit_pick_ids(
        picks,
        audit,
    )

    audit_week_counts(
        picks,
        audit,
    )

    audit_regression_classes(
        picks,
        audit,
    )

    print_summary(
        picks,
        state,
        audit,
    )

    if audit.errors:
        raise RuntimeError(
            "Tracker integrity audit failed "
            f"with {len(audit.errors)} error(s)."
        )

    return audit


if __name__ == "__main__":
    run_audit()
