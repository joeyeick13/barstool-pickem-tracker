from __future__ import annotations

from collections import Counter, defaultdict

from common import PICKS_FILE, STATE_FILE, load_json

from football_identity import (
    SUPPORTED_MARKETS,
    alias_group,
    base_market,
    best_matchup_hints,
    canonical_game_identity,
    canonical_pick_key,
    canonical_team,
    clean_text,
    is_ambiguous_hint,
    normalize_bet_type,
    norm,
    safe_float,
    side_identity,
    spread_line_from_selection,
    split_matchup,
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

AMBIGUOUS_CONTEXT_GROUPS = {
    "osu": {
        "ohio state",
        "oklahoma state",
        "oregon state",
    },
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
# SHARED MATCHUP / ALIAS HELPERS
# ============================================================

def contextual_candidates(value):
    return (
        AMBIGUOUS_CONTEXT_GROUPS.get(
            norm(value),
            set(),
        )
        or set()
    )


def identity_aliases(value):
    """
    Build a normalized alias set for a team identity.

    This intentionally mirrors the shared resolver's identity
    behavior without performing another ESPN network request.
    """

    value = clean_text(value)

    if not value:
        return set()

    aliases = set(
        alias_group(value)
        or []
    )

    normalized = norm(value)

    if normalized:
        aliases.add(normalized)

    canonical = canonical_team(value)

    if canonical:
        canonical_normalized = norm(
            canonical
        )

        if canonical_normalized:
            aliases.add(
                canonical_normalized
            )

        aliases.update(
            alias_group(
                canonical
            )
            or []
        )

    return {
        norm(alias)
        for alias in aliases
        if norm(alias)
    }


def identity_matches(
    first,
    second,
    *,
    allow_contextual_ambiguous=False,
):
    """
    Compare two team identities using the same alias philosophy
    as espn_resolver.py.

    Bare ambiguous aliases such as OSU are unsafe by themselves.
    They are allowed only when evaluating a two-team matchup,
    where the second team supplies context.
    """

    first = clean_text(first)
    second = clean_text(second)

    if not first or not second:
        return False

    first_norm = norm(first)
    second_norm = norm(second)

    if first_norm == second_norm:
        return True

    first_ambiguous = is_ambiguous_hint(
        first
    )

    second_ambiguous = is_ambiguous_hint(
        second
    )

    if first_ambiguous:
        if not allow_contextual_ambiguous:
            return False

        candidates = contextual_candidates(
            first
        )

        second_aliases = identity_aliases(
            second
        )

        for candidate in candidates:
            if (
                identity_aliases(candidate)
                & second_aliases
            ):
                return True

        return False

    if second_ambiguous:
        if not allow_contextual_ambiguous:
            return False

        candidates = contextual_candidates(
            second
        )

        first_aliases = identity_aliases(
            first
        )

        for candidate in candidates:
            if (
                identity_aliases(candidate)
                & first_aliases
            ):
                return True

        return False

    return bool(
        identity_aliases(first)
        & identity_aliases(second)
    )


def pair_matches_pair(
    first_pair,
    second_pair,
):
    """
    Compare two unordered football matchups.

    Contextual ambiguous aliases are allowed because both sides
    of the matchup are available.

    Example:
        OSU / TEX
        Ohio State / Texas

    correctly resolves as the same game.
    """

    if (
        not first_pair
        or not second_pair
        or len(first_pair) != 2
        or len(second_pair) != 2
    ):
        return False

    a, b = first_pair
    x, y = second_pair

    direct = (
        identity_matches(
            a,
            x,
            allow_contextual_ambiguous=True,
        )
        and identity_matches(
            b,
            y,
            allow_contextual_ambiguous=True,
        )
    )

    if direct:
        return True

    reverse = (
        identity_matches(
            a,
            y,
            allow_contextual_ambiguous=True,
        )
        and identity_matches(
            b,
            x,
            allow_contextual_ambiguous=True,
        )
    )

    return reverse


def pick_matchup_hints(pick):
    """
    Return the best available two-team identity for the wager.
    """

    explicit = split_matchup(
        pick.get("matchup")
    )

    if len(explicit) == 2:
        return explicit

    team = clean_text(
        pick.get("team")
    )

    opponent = clean_text(
        pick.get("opponent")
    )

    if team and opponent:
        return (
            team,
            opponent,
        )

    hints = best_matchup_hints(
        pick
    )

    if len(hints) == 2:
        return tuple(hints)

    return tuple()


def authoritative_event_hints(pick):
    """
    schedule_enrich.py refreshes event_matchup directly from the
    locked ESPN event immediately before this audit runs.

    Therefore event_matchup is our authoritative matchup text for
    validating the stored event_id without making another full
    ESPN scoreboard pass.
    """

    matchup = clean_text(
        pick.get(
            "event_matchup"
        )
    )

    if not matchup:
        return tuple()

    hints = split_matchup(
        matchup
    )

    if len(hints) == 2:
        return hints

    return tuple()


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

            hints = pick_matchup_hints(
                pick
            )

            if len(hints) != 2:
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
# AUTHORITATIVE ESPN EVENT MATCHUP VALIDATION
# ============================================================

def audit_event_matchups(
    picks,
    audit,
):
    """
    Validate every provisional locked wager against the ESPN
    matchup metadata refreshed by schedule_enrich.py.

    This is deliberately different from comparing stored wager
    rows to one another.

    event_matchup comes from the actual locked ESPN event.
    The wager matchup comes from the Barstool extraction.

    A mismatch between those two is a genuine integrity problem.

    Contextual aliases such as:
        OSU vs TEX
        Ohio State vs Texas

    are treated as equivalent when the second team constrains
    the ambiguous alias.
    """

    for pick in picks:
        if not is_cfb(pick):
            continue

        if is_official(pick):
            continue

        event_id = str(
            pick.get("event_id")
            or ""
        ).strip()

        if not event_id:
            continue

        event_matchup = clean_text(
            pick.get(
                "event_matchup"
            )
        )

        if not event_matchup:
            audit.error(
                "EVENT_METADATA_MISSING",
                (
                    f"{row_label(pick)} "
                    f"| event={event_id} "
                    "has no refreshed ESPN "
                    "event_matchup metadata."
                ),
            )
            continue

        event_hints = (
            authoritative_event_hints(
                pick
            )
        )

        if len(event_hints) != 2:
            audit.error(
                "EVENT_MATCHUP_UNREADABLE",
                (
                    f"{row_label(pick)} "
                    f"| event={event_id} "
                    f"| ESPN matchup="
                    f"{event_matchup!r}"
                ),
            )
            continue

        wager_hints = (
            pick_matchup_hints(
                pick
            )
        )

        if len(wager_hints) != 2:
            audit.warning(
                "WAGER_MATCHUP_UNAVAILABLE",
                (
                    f"{row_label(pick)} "
                    f"| event={event_id} "
                    f"| ESPN={event_matchup} "
                    "| wager does not contain "
                    "a complete two-team identity "
                    "for independent verification."
                ),
            )
            continue

        if not pair_matches_pair(
            wager_hints,
            event_hints,
        ):
            audit.error(
                "ESPN_EVENT_MISMATCH",
                (
                    f"{row_label(pick)} "
                    f"| event={event_id} "
                    f"| wager matchup="
                    f"{wager_hints[0]} vs "
                    f"{wager_hints[1]} "
                    f"| ESPN matchup="
                    f"{event_hints[0]} vs "
                    f"{event_hints[1]}"
                ),
            )


# ============================================================
# SAME EVENT-ID METADATA CONSISTENCY
# ============================================================

def audit_event_metadata_consistency(
    picks,
    audit,
):
    """
    Every row sharing an ESPN event_id should have the same
    authoritative ESPN matchup metadata after schedule refresh.

    This catches stale/corrupted event metadata without assuming
    the wager text itself is authoritative.
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

        by_event[event_id].append(
            pick
        )

    for event_id, rows in (
        by_event.items()
    ):
        event_pairs = []

        for pick in rows:
            hints = (
                authoritative_event_hints(
                    pick
                )
            )

            if len(hints) != 2:
                continue

            event_pairs.append(
                (
                    hints,
                    pick,
                )
            )

        if len(event_pairs) <= 1:
            continue

        reference_hints = (
            event_pairs[0][0]
        )

        conflicts = []

        for hints, pick in (
            event_pairs[1:]
        ):
            if not pair_matches_pair(
                reference_hints,
                hints,
            ):
                conflicts.append(
                    (
                        hints,
                        pick,
                    )
                )

        if conflicts:
            details = [
                (
                    f"{picker_name(pick)} "
                    f"{selection_text(pick)} "
                    f"=> {hints[0]} vs "
                    f"{hints[1]}"
                )
                for hints, pick
                in conflicts
            ]

            audit.error(
                "EVENT_METADATA_CONFLICT",
                (
                    f"ESPN event {event_id} "
                    "has inconsistent refreshed "
                    "event_matchup metadata: "
                    + " || ".join(details)
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

        if (
            pick.get(
                "ingest_validation_version"
            )
            == 3
        ):
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
# GENERIC REGRESSION CLASSES
# ============================================================

def audit_regression_classes(
    picks,
    audit,
):
    """
    Generic regression tests derived from historical failure
    classes.

    There are intentionally NO Week-specific event IDs,
    matchup patches, or weekly correction tables here.
    """

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

        distinct_games = []

        for pick in rows:
            hints = (
                pick_matchup_hints(
                    pick
                )
            )

            if len(hints) != 2:
                continue

            if not any(
                pair_matches_pair(
                    hints,
                    existing,
                )
                for existing
                in distinct_games
            ):
                distinct_games.append(
                    hints
                )

        if len(distinct_games) <= 1:
            continue

        canonical_keys = {
            repr(
                canonical_pick_key(
                    pick
                )
            )
            for pick in rows
        }

        if (
            len(canonical_keys)
            < len(distinct_games)
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

        distinct_games = []

        for pick in rows:
            hints = (
                pick_matchup_hints(
                    pick
                )
            )

            if len(hints) != 2:
                continue

            if not any(
                pair_matches_pair(
                    hints,
                    existing,
                )
                for existing
                in distinct_games
            ):
                distinct_games.append(
                    hints
                )

        if len(distinct_games) <= 1:
            continue

        canonical_keys = {
            repr(
                canonical_pick_key(
                    pick
                )
            )
            for pick in rows
        }

        if (
            len(canonical_keys)
            < len(distinct_games)
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

    audit_event_matchups(
        picks,
        audit,
    )

    audit_event_metadata_consistency(
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
