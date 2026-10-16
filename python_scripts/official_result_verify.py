from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from common import now_iso
from football_identity import (
    base_market,
    clean_text,
    normalize_bet_type,
    side_identity,
)
from espn_resolver import (
    build_complete_week_slate,
    competitor_display_name,
    competitor_identity_anchor,
    competitors as resolver_competitors,
    event_date,
    event_id,
    event_matchup_text,
    find_event_by_id,
    resolve_event_detailed,
    source_hint_competitor_score,
    source_structural_event_evidence,
)
from grade import (
    SUPPORTED,
    completed,
    event_period_scores,
    fetch_summary,
    final_score_text,
    grade_market,
    hydrate_event_from_summary,
    market_period,
    normalize_existing_pick_market,
    normalize_spread_line,
    season_year_for_pick,
)


OFFICIAL_ROW_VERIFICATION_VERSION = 1
OFFICIAL_ROW_VERIFICATION_VERSION_KEY = (
    "official_row_verification_version"
)
OFFICIAL_ROW_VERIFICATION_SUMMARY_KEY = (
    "official_row_verification_summary_v1"
)
OFFICIAL_ROW_RECONCILIATION_REQUIRED_KEY = (
    "official_row_reconciliation_required_weeks"
)
OFFICIAL_ROW_CUMULATIVE_FAILURES_KEY = (
    "official_row_cumulative_contract_failures_v1"
)
PAT_HILL_RECORD_SNAPSHOTS_KEY = (
    "pat_hill_official_record_snapshots_v2"
)

FINAL_RESULTS = {"WIN", "LOSS", "PUSH"}
RESULT_EMOJI = {
    "WIN": "✅",
    "LOSS": "❌",
    "PUSH": "➖",
}
TRACKED_PICKERS = {
    "Rico Bosco",
    "Big Cat",
    "Stool Presidente",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def _picker(pick):
    value = clean_text(pick.get("picker"))
    if value == "Dave Portnoy":
        return "Stool Presidente"
    if value == "Stool Presidente":
        return "Stool Presidente"
    if value == "Big Cat":
        return "Big Cat"
    if value == "Rico Bosco":
        return "Rico Bosco"
    return value


def _week(pick):
    try:
        return int(pick.get("week") or 0)
    except Exception:
        return 0


def _is_official(pick):
    return bool(pick.get("official_reconciled"))


def result_emoji(result):
    return RESULT_EMOJI.get(
        str(result or "").upper()
    )


def _record_tuple_from_rows(rows):
    counts = {"WIN": 0, "LOSS": 0, "PUSH": 0}
    for row in rows:
        value = str(row.get("result") or "").upper()
        if value in counts:
            counts[value] += 1
    return (
        counts["WIN"],
        counts["LOSS"],
        counts["PUSH"],
    )


def _record_tuple(record):
    record = record or {}
    return (
        int(record.get("wins") or 0),
        int(record.get("losses") or 0),
        int(record.get("pushes") or 0),
    )


def _row_fingerprint(pick):
    """Fingerprint only source wager identity, never the corrected result.

    The result is intentionally excluded so an ESPN correction does not make
    the row stale on the next run. If the source selection/line/matchup is ever
    repaired, the fingerprint changes and the row is automatically reverified.
    """
    payload = {
        "picker": _picker(pick),
        "week": _week(pick),
        "selection": clean_text(
            pick.get("source_selection_text")
            or pick.get("selection")
        ),
        "matchup": clean_text(
            pick.get("source_matchup_text")
            or pick.get("matchup")
        ),
        "team": clean_text(
            pick.get("source_team_text")
            or pick.get("team")
        ),
        "opponent": clean_text(pick.get("opponent")),
        "bet_type": normalize_bet_type(pick.get("bet_type")),
        "side": clean_text(pick.get("side")),
        "line": pick.get("line"),
        "official_result_post_id": str(
            pick.get("official_result_post_id") or ""
        ),
        "source_result_marker": str(
            pick.get("source_result_marker")
            or pick.get("pat_hill_image_result")
            or pick.get("result")
            or ""
        ).upper(),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _initialize_source_result_evidence(pick):
    source_marker = str(
        pick.get("source_result_marker")
        or pick.get("pat_hill_image_result")
        or pick.get("result")
        or ""
    ).upper()

    if source_marker in FINAL_RESULTS:
        pick.setdefault(
            "source_result_marker",
            source_marker,
        )
        pick.setdefault(
            "source_result_emoji",
            result_emoji(source_marker),
        )

    pick.setdefault(
        "source_selection_text",
        clean_text(pick.get("selection")) or None,
    )
    pick.setdefault(
        "source_matchup_text",
        clean_text(pick.get("matchup")) or None,
    )
    pick.setdefault(
        "source_team_text",
        clean_text(pick.get("team")) or None,
    )


def _row_is_currently_verified(pick):
    try:
        version = int(
            pick.get("official_row_verification_version") or 0
        )
    except Exception:
        version = 0

    if version < OFFICIAL_ROW_VERIFICATION_VERSION:
        return False

    if str(
        pick.get("official_row_verification_status") or ""
    ).upper() != "VERIFIED":
        return False

    if str(
        pick.get("result_verification_source") or ""
    ).upper() != "ESPN_FINAL_ROW_GRADE":
        return False

    if str(
        pick.get("result") or ""
    ).upper() not in FINAL_RESULTS:
        return False

    if str(
        pick.get("verified_result") or ""
    ).upper() != str(
        pick.get("result") or ""
    ).upper():
        return False

    if str(
        pick.get("official_row_verification_fingerprint") or ""
    ) != _row_fingerprint(pick):
        return False

    if not str(pick.get("event_id") or "").strip():
        return False

    if not str(pick.get("final_score") or "").strip():
        return False

    stored_result = str(pick.get("result") or "").upper()
    expected_emoji = result_emoji(stored_result)
    if expected_emoji is None:
        return False

    if pick.get("result_emoji") != expected_emoji:
        return False

    if pick.get("display_result_emoji") != expected_emoji:
        return False

    source_marker = str(
        pick.get("source_result_marker") or ""
    ).upper()
    if source_marker not in FINAL_RESULTS:
        return False

    if pick.get("source_result_emoji") != result_emoji(source_marker):
        return False

    if source_marker != stored_result:
        history = pick.get("result_correction_history")
        if not (
            pick.get("official_result_corrected") is True
            and pick.get("source_marker_conflict") is True
            and pick.get("result_correction_reason")
            == "SOURCE_MARKER_CONFLICT_WITH_VERIFIED_ESPN_FINAL"
            and isinstance(history, list)
            and len(history) > 0
        ):
            return False

    return True


def _mark_unverified(pick, code, reason, *, method=None):
    pick["official_row_verification_version"] = (
        OFFICIAL_ROW_VERIFICATION_VERSION
    )
    pick["official_row_verification_status"] = "UNVERIFIED"
    pick["official_row_verification_error"] = code
    pick["official_row_verification_reason"] = reason
    pick["official_row_verification_fingerprint"] = _row_fingerprint(pick)
    pick["official_row_verified_at"] = now_iso()
    if method:
        pick["official_row_event_resolution_method"] = method


def _canonical_team_name(comp):
    if not comp:
        return None
    return (
        competitor_identity_anchor(comp)
        or competitor_display_name(comp)
        or None
    )


def _canonicalize_selected_team_for_grading(pick, event):
    """Canonicalize a WORKING COPY only.

    Source selection/team/matchup fields on the stored official row are never
    overwritten. This helper exists solely so the market grader knows exactly
    which ESPN competitor the source selected.
    """
    market = base_market(
        normalize_bet_type(pick.get("bet_type"))
    )

    if market not in {"SPREAD", "MONEYLINE", "TEAM_TOTAL"}:
        return True

    event_comps = resolver_competitors(event)
    if len(event_comps) != 2:
        return False

    evidence = source_structural_event_evidence(
        pick,
        event,
    )

    selected_index = None
    if evidence is not None:
        selected_index = evidence.get(
            "selected_competitor_index"
        )

    if selected_index not in (0, 1):
        hint = clean_text(
            pick.get("source_team_text")
            or side_identity(pick)
        )

        if not hint:
            return False

        scores = [
            source_hint_competitor_score(comp, hint)
            for comp in event_comps
        ]
        best = max(scores)
        if best <= 0 or scores.count(best) != 1:
            return False
        selected_index = scores.index(best)

    selected = event_comps[selected_index]
    opponent = event_comps[1 - selected_index]

    selected_name = _canonical_team_name(selected)
    opponent_name = _canonical_team_name(opponent)

    if not selected_name:
        return False

    pick["team"] = selected_name
    pick["opponent"] = opponent_name

    # TEAM_TOTAL uses side=OVER/UNDER. Spread/ML side is the selected team.
    if market in {"SPREAD", "MONEYLINE"}:
        pick["side"] = selected_name

    return True


def _working_pick_for_grade(source_pick, event):
    working = dict(source_pick)

    # Reuse the production market normalizer even though the stored row is
    # official. We only modify this temporary copy.
    working["official_reconciled"] = False
    normalize_existing_pick_market(working)
    normalize_spread_line(working)
    working["official_reconciled"] = True

    working["bet_type"] = normalize_bet_type(
        working.get("bet_type")
    )

    if not _canonicalize_selected_team_for_grading(
        working,
        event,
    ):
        market = base_market(working.get("bet_type"))
        if market in {"SPREAD", "MONEYLINE", "TEAM_TOTAL"}:
            return None

    return working


def _resolve_row_event(pick, events):
    """Resolve with the same shared resolver used by live event locking."""
    stored_event_id = str(
        pick.get("event_id") or ""
    ).strip()

    if stored_event_id:
        locked = find_event_by_id(
            events,
            stored_event_id,
        )
        if locked is not None:
            return locked, "EXISTING_VERIFIED_EVENT_ID", None

        # A stale historical lock must not silently rematch. The row will be
        # surfaced for manual/source recovery rather than graded against a new
        # game.
        return (
            None,
            "EXISTING_EVENT_ID_MISSING",
            "Stored ESPN event_id is not present in the authoritative weekly slate.",
        )

    resolution = resolve_event_detailed(
        pick,
        events,
    )
    event = resolution.get("event")

    if event is None:
        return (
            None,
            resolution.get("method") or "NO_CONFIDENT_EVENT_MATCH",
            resolution.get("reason") or "No unique ESPN event could be proven.",
        )

    return (
        event,
        resolution.get("method") or "SHARED_RESOLVER",
        None,
    )


def _grade_one_official_row(pick, events):
    _initialize_source_result_evidence(pick)

    event, method, resolution_error = _resolve_row_event(
        pick,
        events,
    )

    if event is None:
        _mark_unverified(
            pick,
            "EVENT_UNRESOLVED",
            resolution_error or "No exact event.",
            method=method,
        )
        return False, False

    event_value = event_id(event)
    if not event_value:
        _mark_unverified(
            pick,
            "EVENT_ID_MISSING",
            "Resolved ESPN event has no event ID.",
            method=method,
        )
        return False, False

    # Result posts are published after the games. Require ESPN to say final;
    # never infer a historical result merely because PAT HILL has a marker.
    if not completed(event):
        _mark_unverified(
            pick,
            "EVENT_NOT_FINAL",
            "Resolved ESPN event is not final.",
            method=method,
        )
        return False, False

    try:
        summary = fetch_summary(event_value)
        event = hydrate_event_from_summary(
            event,
            summary,
        )
    except Exception as exc:
        # Full-game scoreboard objects often already contain final scores, so
        # summary failure alone is not fatal. Period markets below still fail
        # closed if their linescore data cannot be retrieved.
        pick["official_row_summary_warning"] = (
            f"{type(exc).__name__}: {exc}"
        )

    if not completed(event):
        _mark_unverified(
            pick,
            "EVENT_NOT_FINAL_AFTER_HYDRATION",
            "ESPN did not confirm a final event after summary hydration.",
            method=method,
        )
        return False, False

    working = _working_pick_for_grade(
        pick,
        event,
    )

    if working is None:
        _mark_unverified(
            pick,
            "SELECTED_TEAM_UNRESOLVED",
            "Exact selected ESPN competitor could not be proven inside the resolved event.",
            method=method,
        )
        return False, False

    bet_type = normalize_bet_type(
        working.get("bet_type")
    )
    if bet_type not in SUPPORTED:
        _mark_unverified(
            pick,
            "UNSUPPORTED_MARKET",
            f"Unsupported official market: {bet_type}",
            method=method,
        )
        return False, False

    period = market_period(bet_type)
    period_scores = None

    if period in {"FIRST_QUARTER", "FIRST_HALF"}:
        try:
            period_scores = event_period_scores(
                event,
                period,
            )
        except Exception as exc:
            _mark_unverified(
                pick,
                "PERIOD_SCORE_FETCH_FAILED",
                f"{type(exc).__name__}: {exc}",
                method=method,
            )
            return False, False

        if not period_scores:
            _mark_unverified(
                pick,
                "PERIOD_SCORE_UNAVAILABLE",
                f"ESPN {period} score is unavailable.",
                method=method,
            )
            return False, False

    verified_result = grade_market(
        working,
        event,
        period_scores,
    )

    if verified_result not in FINAL_RESULTS:
        _mark_unverified(
            pick,
            "MARKET_NOT_GRADEABLE",
            "Resolved final ESPN event could not grade the exact stored market.",
            method=method,
        )
        return False, False

    previous_result = str(
        pick.get("result") or ""
    ).upper()
    source_result = str(
        pick.get("source_result_marker") or ""
    ).upper()

    # A source-marker correction is about the immutable PAT HILL emoji, not
    # merely whether today's stored result happened to change. A row may have
    # been corrected by an older migration before this verifier existed; we
    # still record the source-marker conflict explicitly and preserve the
    # original emoji forever.
    marker_conflict = (
        source_result in FINAL_RESULTS
        and source_result != verified_result
    )
    storage_changed = previous_result != verified_result
    corrected = marker_conflict or storage_changed

    if corrected:
        history = pick.get("result_correction_history")
        if not isinstance(history, list):
            history = []

        correction = {
            "at": now_iso(),
            "from": (
                source_result
                if marker_conflict
                else previous_result or None
            ),
            "prior_stored_result": previous_result or None,
            "to": verified_result,
            "source_result_marker": source_result or None,
            "event_id": event_value,
            "final_score": final_score_text(event),
            "reason": "SOURCE_MARKER_CONFLICT_WITH_VERIFIED_ESPN_FINAL",
        }

        # Idempotence: do not append the same logical correction repeatedly.
        if not history or any(
            history[-1].get(key) != correction.get(key)
            for key in (
                "from",
                "to",
                "source_result_marker",
                "event_id",
                "reason",
            )
        ):
            history.append(correction)

        pick["result_correction_history"] = history
        pick["official_result_corrected"] = True
        pick["source_marker_conflict"] = bool(marker_conflict)
        pick["result_correction_reason"] = (
            "SOURCE_MARKER_CONFLICT_WITH_VERIFIED_ESPN_FINAL"
        )
        pick["pre_espn_verified_result"] = previous_result or None
    else:
        pick["source_marker_conflict"] = False

    pick["result"] = verified_result
    pick["result_emoji"] = result_emoji(verified_result)
    pick["display_result_emoji"] = result_emoji(verified_result)
    pick["status"] = "FINAL"
    pick["event_id"] = event_value
    pick["competition_id"] = event_value
    pick["event_matchup"] = event_matchup_text(event)
    pick["game_matchup"] = event_matchup_text(event)
    pick["game_time"] = event_date(event)
    pick["event_status"] = "STATUS_FINAL"
    pick["game_match_status"] = "MATCHED"
    pick["game_match_source"] = "ESPN"
    pick["game_match_confidence"] = "OFFICIAL_ROW_VERIFIED"
    pick["official_row_event_resolution_method"] = method
    pick["final_score"] = final_score_text(event)
    pick["grade_source"] = "ESPN"
    pick["graded_at"] = now_iso()
    pick["result_verification_source"] = "ESPN_FINAL_ROW_GRADE"
    pick["verified_result"] = verified_result
    pick["result_verification_event_id"] = event_value
    pick["result_verification_final_score"] = final_score_text(event)
    pick["official_row_verification_version"] = (
        OFFICIAL_ROW_VERIFICATION_VERSION
    )
    pick["official_row_verification_status"] = "VERIFIED"
    pick["official_row_verification_error"] = None
    pick["official_row_verification_reason"] = None
    pick["official_row_verified_at"] = now_iso()

    # The existing official-table convention is one unit per card row.
    pick["profit_units"] = (
        1.0
        if verified_result == "WIN"
        else -1.0
        if verified_result == "LOSS"
        else 0.0
    )

    if period_scores is not None:
        pick["period_score"] = period_scores
    else:
        pick.pop("period_score", None)

    # Useful audit metadata without mutating the literal source fields.
    pick["verified_bet_type"] = bet_type
    pick["verified_line"] = working.get("line")
    pick["verified_team"] = working.get("team")
    pick["verified_opponent"] = working.get("opponent")

    pick["official_row_verification_fingerprint"] = _row_fingerprint(pick)

    return True, corrected


# ============================================================
# WEEK VERIFICATION
# ============================================================

def verify_official_week_rows(
    rows,
    *,
    target_week,
    expected_weekly_records=None,
    force=False,
    events=None,
):
    """Verify every official row in one week against ESPN final data.

    Mutates ``rows`` in memory. No file I/O occurs here, so x_ingest can use
    this inside its week-replacement transaction before committing anything.
    """
    official_rows = [
        row for row in rows
        if _is_official(row) and _week(row) == int(target_week)
    ]

    report = {
        "week": int(target_week),
        "rows": len(official_rows),
        "verified": 0,
        "unverified": 0,
        "corrected": 0,
        "skipped_current": 0,
        "weekly_contract_ok": True,
        "picker_records": {},
        "errors": [],
    }

    if not official_rows:
        report["weekly_contract_ok"] = False
        report["errors"].append("NO_OFFICIAL_ROWS")
        return report

    if events is None:
        season_year = season_year_for_pick(official_rows[0])
        events = build_complete_week_slate(
            official_rows,
            season_year,
            int(target_week),
        )

    for pick in official_rows:
        _initialize_source_result_evidence(pick)

        if not force and _row_is_currently_verified(pick):
            report["verified"] += 1
            report["skipped_current"] += 1
            continue

        verified, corrected = _grade_one_official_row(
            pick,
            events,
        )

        if verified:
            report["verified"] += 1
            if corrected:
                report["corrected"] += 1
                print(
                    "OFFICIAL ROW RESULT CORRECTED FROM VERIFIED ESPN FINAL:",
                    _picker(pick),
                    "| Week", target_week,
                    "|", pick.get("selection"),
                    "| source:", pick.get("source_result_marker"),
                    "-> verified:", pick.get("result"),
                    "| emoji:", pick.get("result_emoji"),
                    "| event:", pick.get("event_id"),
                    "| final:", pick.get("final_score"),
                )
        else:
            report["unverified"] += 1
            print(
                "OFFICIAL ROW NOT VERIFIED:",
                _picker(pick),
                "| Week", target_week,
                "|", pick.get("selection"),
                "| error:", pick.get("official_row_verification_error"),
                "| reason:", pick.get("official_row_verification_reason"),
            )

    for picker in sorted(TRACKED_PICKERS):
        picker_rows = [
            row for row in official_rows
            if _picker(row) == picker
        ]
        actual = _record_tuple_from_rows(picker_rows)
        expected = None
        if expected_weekly_records:
            expected = _record_tuple(
                expected_weekly_records.get(picker)
            )

        verified_count = sum(
            1 for row in picker_rows
            if _row_is_currently_verified(row)
        )

        report["picker_records"][picker] = {
            "actual": actual,
            "rows": len(picker_rows),
            "verified_rows": verified_count,
            "expected": expected,
        }

        if verified_count != len(picker_rows):
            report["weekly_contract_ok"] = False
            report["errors"].append(
                f"{picker}:UNVERIFIED_ROWS:{len(picker_rows)-verified_count}"
            )

        if expected is not None:
            if actual != expected or len(picker_rows) != sum(expected):
                report["weekly_contract_ok"] = False
                report["errors"].append(
                    f"{picker}:RECORD_MISMATCH:{actual}!={expected}"
                )

        print(
            "OFFICIAL ROW VERIFICATION RECORD:",
            "Week", target_week,
            "|", picker,
            "| verified rows:", f"{verified_count}/{len(picker_rows)}",
            "| ESPN row record:", f"{actual[0]}-{actual[1]}-{actual[2]}",
            "| official weekly root:", (
                f"{expected[0]}-{expected[1]}-{expected[2]}"
                if expected is not None
                else "-"
            ),
        )

    return report


# ============================================================
# GLOBAL / IDEMPOTENT BACKFILL
# ============================================================

def verify_all_official_rows(picks, state):
    """Backfill/reverify every official row, then enforce weekly aggregates.

    Current verified rows are skipped without ESPN calls. A source identity
    change invalidates only that row's fingerprint and causes deterministic
    re-verification on the next run.
    """
    official_rows = [
        pick for pick in picks
        if _is_official(pick)
    ]

    if not official_rows:
        state[OFFICIAL_ROW_VERIFICATION_VERSION_KEY] = (
            OFFICIAL_ROW_VERIFICATION_VERSION
        )
        state[OFFICIAL_ROW_VERIFICATION_SUMMARY_KEY] = {
            "version": OFFICIAL_ROW_VERIFICATION_VERSION,
            "rows": 0,
            "verified": 0,
            "unverified": 0,
            "corrected": 0,
            "updated_at": now_iso(),
        }
        state[OFFICIAL_ROW_RECONCILIATION_REQUIRED_KEY] = []
        state[OFFICIAL_ROW_CUMULATIVE_FAILURES_KEY] = []
        return picks, state[OFFICIAL_ROW_VERIFICATION_SUMMARY_KEY]

    snapshots = (
        state.get(PAT_HILL_RECORD_SNAPSHOTS_KEY, {})
        or {}
    )

    by_week = defaultdict(list)
    for row in official_rows:
        by_week[_week(row)].append(row)

    overall = {
        "version": OFFICIAL_ROW_VERIFICATION_VERSION,
        "rows": len(official_rows),
        "verified": 0,
        "unverified": 0,
        "corrected": 0,
        "skipped_current": 0,
        "weeks": {},
        "cumulative_contract_ok": True,
        "cumulative_failures": [],
        "updated_at": now_iso(),
    }

    reconciliation_required = []

    for week in sorted(by_week):
        rows = by_week[week]
        expected = (
            (snapshots.get(str(week)) or {}).get("weekly_records")
            or None
        )

        needs_network = any(
            not _row_is_currently_verified(row)
            for row in rows
        )

        try:
            report = verify_official_week_rows(
                rows,
                target_week=week,
                expected_weekly_records=expected,
                force=False,
                events=None if needs_network else [],
            )
        except Exception as exc:
            # Do not destroy already-verified rows on an ESPN outage. Mark only
            # stale rows as unverified so the audit blocks publication until a
            # later deterministic retry succeeds.
            for row in rows:
                if not _row_is_currently_verified(row):
                    _initialize_source_result_evidence(row)
                    _mark_unverified(
                        row,
                        "WEEK_ESPN_LOAD_FAILED",
                        f"{type(exc).__name__}: {exc}",
                    )

            report = {
                "week": week,
                "rows": len(rows),
                "verified": sum(
                    1 for row in rows
                    if _row_is_currently_verified(row)
                ),
                "unverified": sum(
                    1 for row in rows
                    if not _row_is_currently_verified(row)
                ),
                "corrected": 0,
                "skipped_current": sum(
                    1 for row in rows
                    if _row_is_currently_verified(row)
                ),
                "weekly_contract_ok": False,
                "picker_records": {},
                "errors": [
                    f"WEEK_ESPN_LOAD_FAILED:{type(exc).__name__}:{exc}"
                ],
            }

        overall["weeks"][str(week)] = report
        for key in (
            "verified",
            "unverified",
            "corrected",
            "skipped_current",
        ):
            overall[key] += int(report.get(key) or 0)

        if not report.get("weekly_contract_ok"):
            reconciliation_required.append(int(week))

    # --------------------------------------------------------
    # CUMULATIVE PAT HILL CONTRACT
    # --------------------------------------------------------
    # Once every exact row is independently graded, also compare the running
    # game-by-game totals through each official week to that week's separately
    # parsed season standings. This never changes a row to make the math fit;
    # it only proves consistency or surfaces a source/identity conflict.
    cumulative_failures = []

    for week in sorted(by_week):
        snapshot = snapshots.get(str(week)) or {}
        season = snapshot.get("season_standings") or {}
        if len(season) != len(TRACKED_PICKERS):
            continue

        for picker in sorted(TRACKED_PICKERS):
            running_rows = [
                row
                for row in official_rows
                if (
                    1 <= _week(row) <= int(week)
                    and _picker(row) == picker
                )
            ]
            actual = _record_tuple_from_rows(running_rows)
            expected = _record_tuple(season.get(picker))
            verified_count = sum(
                1 for row in running_rows
                if _row_is_currently_verified(row)
            )

            if (
                verified_count != len(running_rows)
                or actual != expected
                or len(running_rows) != sum(expected)
            ):
                overall["cumulative_contract_ok"] = False
                failure = {
                    "week": int(week),
                    "picker": picker,
                    "actual": actual,
                    "expected": expected,
                    "rows": len(running_rows),
                    "verified_rows": verified_count,
                }
                cumulative_failures.append(failure)
                print(
                    "OFFICIAL ROW CUMULATIVE CONTRACT MISMATCH:",
                    "through Week", week,
                    "|", picker,
                    "| verified game-by-game:",
                    f"{actual[0]}-{actual[1]}-{actual[2]}",
                    "| official standings:",
                    f"{expected[0]}-{expected[1]}-{expected[2]}",
                    "| rows:", f"{len(running_rows)}/{sum(expected)}",
                )

    overall["cumulative_failures"] = cumulative_failures
    state[OFFICIAL_ROW_CUMULATIVE_FAILURES_KEY] = cumulative_failures

    if overall["unverified"] == 0:
        state[OFFICIAL_ROW_VERIFICATION_VERSION_KEY] = (
            OFFICIAL_ROW_VERIFICATION_VERSION
        )
    else:
        # Explicitly do not claim the backfill is current.
        state[OFFICIAL_ROW_VERIFICATION_VERSION_KEY] = 0

    state[OFFICIAL_ROW_RECONCILIATION_REQUIRED_KEY] = sorted(
        set(reconciliation_required)
    )
    state[OFFICIAL_ROW_VERIFICATION_SUMMARY_KEY] = overall

    print()
    print("====================================")
    print("OFFICIAL ROW ESPN VERIFICATION")
    print("====================================")
    print("Official rows:", overall["rows"])
    print("Verified:", overall["verified"])
    print("Unverified:", overall["unverified"])
    print("Result/emoji corrections:", overall["corrected"])
    print("Already-current rows skipped:", overall["skipped_current"])
    print(
        "Cumulative standings contract:",
        "PASSED" if overall.get("cumulative_contract_ok") else "FAILED",
    )
    print(
        "Weeks requiring source reconciliation:",
        state[OFFICIAL_ROW_RECONCILIATION_REQUIRED_KEY],
    )
    print("====================================")

    return picks, overall
