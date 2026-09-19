from __future__ import annotations

import re
from datetime import datetime, timezone

import requests

from common import (
    PICKS_FILE,
    american_profit,
    load_json,
    now_iso,
    save_json,
)

from football_identity import (
    base_market,
    clean_text,
    market_period,
    normalize_bet_type,
    safe_float,
    side_identity,
    spread_line_from_selection,
    teams_equivalent,
    total_direction,
)

from espn_resolver import (
    build_complete_week_slate,
    event_date,
    event_matchup_text,
    find_event_by_id,
    resolve_event_detailed,
)


# ============================================================
# ESPN CONFIG
# ============================================================

ESPN_BASE = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/college-football"
)

ESPN_SUMMARY = f"{ESPN_BASE}/summary"

ESPN_CORE = (
    "https://sports.core.api.espn.com/v2/"
    "sports/football/leagues/"
    "college-football"
)


# ============================================================
# SUPPORTED MARKETS
# ============================================================

SUPPORTED = {
    "SPREAD",
    "TOTAL",
    "MONEYLINE",
    "TEAM_TOTAL",

    "FIRST_QUARTER_SPREAD",
    "FIRST_QUARTER_TOTAL",
    "FIRST_QUARTER_MONEYLINE",
    "FIRST_QUARTER_TEAM_TOTAL",

    "FIRST_HALF_SPREAD",
    "FIRST_HALF_TOTAL",
    "FIRST_HALF_MONEYLINE",
    "FIRST_HALF_TEAM_TOTAL",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def request_json(url, params=None):
    response = requests.get(
        url,
        params=params,
        timeout=30,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.espn.com",
            "Referer": "https://www.espn.com/",
        },
    )

    response.raise_for_status()
    return response.json()


def week_number(pick):
    try:
        return int(
            pick.get("week") or 1
        )
    except Exception:
        return 1


def season_year_for_pick(pick):
    if pick.get("season"):
        try:
            return int(pick["season"])
        except Exception:
            pass

    posted = parse_datetime(
        pick.get("posted_at")
    )

    if posted:
        return posted.year

    return datetime.now(
        timezone.utc
    ).year


def picker_name(pick):
    return (
        pick.get("picker")
        or "Unknown"
    )


def pick_label(pick):
    return (
        pick.get("selection")
        or pick.get("raw_text")
        or "Unknown selection"
    )


# ============================================================
# OFFICIAL RESULT LOCK
# ============================================================

def is_official_result(pick):
    """
    PAT HILL STANDINGS is final authority.

    Officially reconciled rows are immutable to ESPN grading.
    """

    return bool(
        pick.get("official_reconciled")
    )


# ============================================================
# MARKET NORMALIZATION
# ============================================================

def normalize_existing_pick_market(pick):
    """
    Upgrade older provisional market labels.

    Official PAT HILL rows are never modified.
    """

    if is_official_result(pick):
        return

    text = str(
        pick.get("selection") or ""
    ).lower()

    text = (
        text
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("½", ".5")
    )

    first_quarter = bool(
        re.search(
            r"\b(?:1q|1st\s*q|first\s*quarter)\b",
            text,
            flags=re.I,
        )
    )

    first_half = bool(
        re.search(
            r"\b(?:1h|1st\s*h|first\s*half)\b",
            text,
            flags=re.I,
        )
    )

    team_total = bool(
        re.search(
            r"\b(?:tt|team\s*total)\b",
            text,
            flags=re.I,
        )
    )

    if team_total:

        if first_quarter:
            pick["bet_type"] = (
                "FIRST_QUARTER_TEAM_TOTAL"
            )

        elif first_half:
            pick["bet_type"] = (
                "FIRST_HALF_TEAM_TOTAL"
            )

        else:
            pick["bet_type"] = "TEAM_TOTAL"

        compact = re.search(
            r"\b(?:tt|team\s*total)"
            r"\s*(o|u)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if compact:
            pick["side"] = (
                "OVER"
                if compact.group(1).lower() == "o"
                else "UNDER"
            )

            pick["line"] = float(
                compact.group(2)
            )

        verbose = re.search(
            r"\b(?:tt|team\s*total)"
            r"\s*(over|under)\s*"
            r"([0-9]+(?:\.[0-9]+)?)",
            text,
            flags=re.I,
        )

        if verbose:
            pick["side"] = (
                verbose.group(1).upper()
            )

            pick["line"] = float(
                verbose.group(2)
            )

        return

    if first_quarter or first_half:

        prefix = (
            "FIRST_QUARTER"
            if first_quarter
            else "FIRST_HALF"
        )

        if re.search(
            r"\b(?:over|under)\b",
            text,
        ):
            pick["bet_type"] = (
                f"{prefix}_TOTAL"
            )

        elif re.search(
            r"\b(?:ml|moneyline)\b",
            text,
        ):
            pick["bet_type"] = (
                f"{prefix}_MONEYLINE"
            )

        elif re.search(
            r"[+-]\s*\d+(?:\.\d+)?",
            text,
        ):
            pick["bet_type"] = (
                f"{prefix}_SPREAD"
            )

        return

    pick["bet_type"] = normalize_bet_type(
        pick.get("bet_type")
    )


def normalize_spread_line(pick):
    """
    Make the visible bettor-facing spread selection authoritative
    for the sign of the stored numeric line.

    Example:
        Oregon -22.5
    must grade as -22.5 even if historical extraction stored +22.5.
    """

    if is_official_result(pick):
        return False

    bet_type = normalize_bet_type(
        pick.get("bet_type")
    )

    if base_market(bet_type) != "SPREAD":
        return False

    selection_line = (
        spread_line_from_selection(pick)
    )

    if selection_line is None:
        return False

    stored_line = safe_float(
        pick.get("line")
    )

    if (
        stored_line is not None
        and abs(
            stored_line - selection_line
        ) < 0.000001
    ):
        return False

    pick["line"] = selection_line

    print(
        "SPREAD LINE NORMALIZED:",
        picker_name(pick),
        "|",
        pick_label(pick),
        "| stored:",
        stored_line,
        "-> signed:",
        selection_line,
    )

    return True


# ============================================================
# ESPN EVENT HELPERS
# ============================================================

def competitions(event):
    return (
        event.get("competitions")
        or []
    )


def competitors(event):
    comps = competitions(event)

    if not comps:
        return []

    return (
        comps[0].get("competitors")
        or []
    )


def event_status(event):
    status = (
        event.get("status")
        or {}
    )

    if status:
        return status

    comps = competitions(event)

    if comps:
        return (
            comps[0].get("status")
            or {}
        )

    return {}


def completed(event):
    return bool(
        (
            event_status(event)
            .get("type")
            or {}
        ).get("completed")
    )


def score_value(comp):
    value = comp.get("score")

    if value is None:
        return None

    if isinstance(
        value,
        (str, int, float),
    ):
        try:
            return float(value)
        except Exception:
            return None

    if isinstance(value, dict):

        for key in (
            "value",
            "displayValue",
            "score",
        ):
            candidate = value.get(key)

            if isinstance(
                candidate,
                dict,
            ):
                candidate = (
                    candidate.get("value")
                    or candidate.get(
                        "displayValue"
                    )
                )

            try:
                if candidate is not None:
                    return float(candidate)
            except Exception:
                continue

    return None


def team_display_name(comp):
    team = (
        comp.get("team")
        or {}
    )

    return (
        team.get("displayName")
        or team.get("shortDisplayName")
        or team.get("location")
        or team.get("abbreviation")
        or ""
    )


def team_id_from_comp(comp):
    if not comp:
        return None

    team = (
        comp.get("team")
        or {}
    )

    value = team.get("id")

    if value is None:
        return None

    return str(value)


def final_score_text(event):
    output = []

    for comp in competitors(event):

        team = (
            comp.get("team")
            or {}
        )

        label = (
            team.get("abbreviation")
            or team.get("shortDisplayName")
            or team.get("displayName")
            or "Team"
        )

        score = score_value(comp)

        if score is None:
            display = "?"

        elif float(score).is_integer():
            display = str(int(score))

        else:
            display = str(score)

        output.append(
            f"{label} {display}"
        )

    return " - ".join(output)


# ============================================================
# SELECTED TEAM MATCHING
# ============================================================

def selected_comp(pick, event):
    """
    Resolve the selected team only inside an already-locked ESPN event.

    This is NOT event resolution.

    Event resolution belongs exclusively to espn_resolver.py.
    """

    hint = side_identity(pick)

    if not hint:
        return None

    matches = []

    for comp in competitors(event):

        candidate = team_display_name(comp)

        if not candidate:
            continue

        if teams_equivalent(
            hint,
            candidate,
        ):
            matches.append(comp)
            continue

        team = (
            comp.get("team")
            or {}
        )

        for value in (
            team.get("shortDisplayName"),
            team.get("location"),
            team.get("abbreviation"),
        ):
            if (
                value
                and teams_equivalent(
                    hint,
                    value,
                )
            ):
                matches.append(comp)
                break

    # Deduplicate by ESPN team ID.
    unique = {}

    for comp in matches:
        team_id = team_id_from_comp(comp)

        if team_id:
            unique[team_id] = comp

    if len(unique) == 1:
        return next(
            iter(unique.values())
        )

    return None


def opponent_comp(selected, event):
    if selected is None:
        return None

    selected_id = (
        team_id_from_comp(selected)
    )

    if not selected_id:
        return None

    others = []

    for comp in competitors(event):

        team_id = (
            team_id_from_comp(comp)
        )

        if (
            team_id
            and team_id != selected_id
        ):
            others.append(comp)

    if len(others) == 1:
        return others[0]

    return None


# ============================================================
# ESPN SUMMARY
# ============================================================

def fetch_summary(event_id):
    return request_json(
        ESPN_SUMMARY,
        {
            "event": str(event_id)
        },
    )


def summary_competitors(summary):
    header = (
        summary.get("header")
        or {}
    )

    competitions_list = (
        header.get("competitions")
        or []
    )

    if not competitions_list:
        return []

    return (
        competitions_list[0]
        .get("competitors")
        or []
    )


def hydrate_event_from_summary(
    event,
    summary,
):
    summary_comps = (
        summary_competitors(summary)
    )

    if not summary_comps:
        return event

    hydrated = dict(event)

    competitions_list = [
        dict(comp)
        for comp in competitions(event)
    ]

    if not competitions_list:
        competitions_list = [{}]

    competition = dict(
        competitions_list[0]
    )

    competition[
        "competitors"
    ] = summary_comps

    header = (
        summary.get("header")
        or {}
    )

    header_competitions = (
        header.get("competitions")
        or []
    )

    if header_competitions:

        summary_competition = (
            header_competitions[0]
            or {}
        )

        if summary_competition.get(
            "status"
        ):
            competition[
                "status"
            ] = summary_competition[
                "status"
            ]

            hydrated[
                "status"
            ] = summary_competition[
                "status"
            ]

    competitions_list[0] = competition

    hydrated[
        "competitions"
    ] = competitions_list

    return hydrated


# ============================================================
# PERIOD LINE SCORES
# ============================================================

def core_linescore_url(
    event_id,
    team_id,
):
    return (
        f"{ESPN_CORE}/"
        f"events/{event_id}/"
        f"competitions/{event_id}/"
        f"competitors/{team_id}/"
        "linescores"
    )


def extract_period_value(item):
    period = item.get("period")

    if isinstance(period, dict):
        period = (
            period.get("number")
            or period.get("value")
        )

    if period is None:
        period = (
            item.get("periodNumber")
            or item.get("number")
        )

    value = item.get("value")

    if value is None:
        value = item.get(
            "displayValue"
        )

    if value is None:
        value = item.get("score")

    if isinstance(value, dict):
        value = (
            value.get("value")
            or value.get(
                "displayValue"
            )
        )

    try:
        return (
            int(period),
            float(value),
        )
    except Exception:
        return (
            None,
            None,
        )


def fetch_team_linescores(
    event_id,
    team_id,
):
    payload = request_json(
        core_linescore_url(
            event_id,
            team_id,
        )
    )

    items = (
        payload.get("items")
        or []
    )

    output = {}

    for item in items:

        period, value = (
            extract_period_value(item)
        )

        if (
            period is not None
            and value is not None
        ):
            output[period] = value

    return output


def event_period_scores(
    event,
    period,
):
    event_value = str(
        event.get("id")
        or ""
    )

    if not event_value:
        return None

    output = {}

    for comp in competitors(event):

        team_id = (
            team_id_from_comp(comp)
        )

        if not team_id:
            return None

        try:
            lines = (
                fetch_team_linescores(
                    event_value,
                    team_id,
                )
            )

        except Exception as exc:
            print(
                "PERIOD SCORE FETCH FAILED:",
                event_value,
                "| team:",
                team_id,
                "|",
                exc,
            )

            return None

        if period == "FIRST_QUARTER":

            if 1 not in lines:
                return None

            output[
                team_id
            ] = lines[1]

        elif period == "FIRST_HALF":

            if (
                1 not in lines
                or 2 not in lines
            ):
                return None

            output[
                team_id
            ] = (
                lines[1]
                + lines[2]
            )

        else:
            return None

    if len(output) != 2:
        return None

    return output


# ============================================================
# GRADING MATH
# ============================================================

def compare_total(
    score,
    line,
    direction,
):
    if (
        score is None
        or line is None
        or direction not in {
            "OVER",
            "UNDER",
        }
    ):
        return None

    if score == line:
        return "PUSH"

    if direction == "OVER":
        return (
            "WIN"
            if score > line
            else "LOSS"
        )

    return (
        "WIN"
        if score < line
        else "LOSS"
    )


def compare_spread(
    selected_score,
    opponent_score,
    line,
):
    if (
        selected_score is None
        or opponent_score is None
        or line is None
    ):
        return None

    adjusted = (
        selected_score + line
    )

    if adjusted == opponent_score:
        return "PUSH"

    return (
        "WIN"
        if adjusted > opponent_score
        else "LOSS"
    )


def compare_moneyline(
    selected_score,
    opponent_score,
):
    if (
        selected_score is None
        or opponent_score is None
    ):
        return None

    if (
        selected_score
        == opponent_score
    ):
        return "PUSH"

    return (
        "WIN"
        if selected_score
        > opponent_score
        else "LOSS"
    )


def full_game_scores(event):
    comps = competitors(event)

    if len(comps) != 2:
        return None

    output = {}

    for comp in comps:

        team_id = (
            team_id_from_comp(comp)
        )

        score = score_value(comp)

        if (
            not team_id
            or score is None
        ):
            return None

        output[
            team_id
        ] = score

    if len(output) != 2:
        return None

    return output


def grade_total_market(
    pick,
    scores,
):
    if len(scores) != 2:
        return None

    total = sum(
        scores.values()
    )

    return compare_total(
        total,
        safe_float(
            pick.get("line")
        ),
        total_direction(pick),
    )


def grade_team_total_market(
    pick,
    event,
    scores,
):
    selected = selected_comp(
        pick,
        event,
    )

    if selected is None:
        return None

    selected_id = (
        team_id_from_comp(selected)
    )

    if (
        not selected_id
        or selected_id
        not in scores
    ):
        return None

    return compare_total(
        scores[selected_id],
        safe_float(
            pick.get("line")
        ),
        total_direction(pick),
    )


def grade_spread_market(
    pick,
    event,
    scores,
):
    selected = selected_comp(
        pick,
        event,
    )

    opponent = opponent_comp(
        selected,
        event,
    )

    selected_id = (
        team_id_from_comp(selected)
    )

    opponent_id = (
        team_id_from_comp(opponent)
    )

    if (
        not selected_id
        or not opponent_id
        or selected_id
        not in scores
        or opponent_id
        not in scores
    ):
        return None

    selection_line = (
        spread_line_from_selection(pick)
    )

    if selection_line is not None:
        line = selection_line
        pick["line"] = selection_line

    else:
        line = safe_float(
            pick.get("line")
        )

    return compare_spread(
        scores[selected_id],
        scores[opponent_id],
        line,
    )


def grade_moneyline_market(
    pick,
    event,
    scores,
):
    selected = selected_comp(
        pick,
        event,
    )

    opponent = opponent_comp(
        selected,
        event,
    )

    selected_id = (
        team_id_from_comp(selected)
    )

    opponent_id = (
        team_id_from_comp(opponent)
    )

    if (
        not selected_id
        or not opponent_id
        or selected_id
        not in scores
        or opponent_id
        not in scores
    ):
        return None

    return compare_moneyline(
        scores[selected_id],
        scores[opponent_id],
    )


def grade_market(
    pick,
    event,
    period_scores=None,
):
    bet_type = normalize_bet_type(
        pick.get("bet_type")
    )

    market = base_market(
        bet_type
    )

    period = market_period(
        bet_type
    )

    if period == "FULL_GAME":
        scores = full_game_scores(
            event
        )
    else:
        scores = period_scores

    if not scores:
        return None

    if market == "TOTAL":
        return grade_total_market(
            pick,
            scores,
        )

    if market == "TEAM_TOTAL":
        return grade_team_total_market(
            pick,
            event,
            scores,
        )

    if market == "SPREAD":
        return grade_spread_market(
            pick,
            event,
            scores,
        )

    if market == "MONEYLINE":
        return grade_moneyline_market(
            pick,
            event,
            scores,
        )

    return None


# ============================================================
# PROFIT / STATUS
# ============================================================

def pick_units(pick):
    value = safe_float(
        pick.get("units")
    )

    if value is None:
        return 1.0

    return value


def pick_odds(pick):
    value = pick.get("odds")

    if value in (
        None,
        "",
    ):
        return -110

    try:
        return int(value)
    except Exception:
        return -110


def calculate_profit(
    pick,
    result,
):
    try:
        return american_profit(
            pick_odds(pick),
            pick_units(pick),
            result,
        )

    except Exception:

        if result == "LOSS":
            return (
                -1 * pick_units(pick)
            )

        if result == "PUSH":
            return 0.0

        odds = pick_odds(pick)
        units = pick_units(pick)

        if odds > 0:
            return (
                units
                * odds
                / 100.0
            )

        return (
            units
            * 100.0
            / abs(odds)
        )


def clear_grade(pick):
    """
    Clear ESPN-derived grade fields.

    event_id is deliberately preserved.
    """

    for key in (
        "result",
        "status",
        "final_score",
        "profit",
        "graded_at",
        "period_score",
        "grade_source",
        "grading_error",
    ):
        pick.pop(
            key,
            None,
        )


def mark_open(pick):
    pick["status"] = "OPEN"
    pick["result"] = None


def mark_review(
    pick,
    reason,
):
    pick["status"] = "OPEN"
    pick["result"] = None
    pick["grading_error"] = reason


def apply_grade(
    pick,
    event,
    result,
):
    pick["result"] = result
    pick["status"] = "FINAL"

    pick["event_id"] = str(
        event.get("id")
        or ""
    )

    pick[
        "final_score"
    ] = final_score_text(event)

    pick[
        "profit"
    ] = calculate_profit(
        pick,
        result,
    )

    pick["graded_at"] = now_iso()
    pick["grade_source"] = "ESPN"


# ============================================================
# EVENT METADATA
# ============================================================

def refresh_event_metadata(
    pick,
    event,
):
    event_id = str(
        event.get("id")
        or ""
    )

    if event_id:
        pick["event_id"] = event_id

    game_time = event_date(event)

    if game_time:
        pick["game_time"] = game_time

    matchup = event_matchup_text(
        event
    )

    if matchup:
        pick[
            "event_matchup"
        ] = matchup


# ============================================================
# AUDIT DISPLAY
# ============================================================

def print_week_audit(
    picks,
    week,
):
    print()
    print("=" * 70)
    print(f"WEEK {week} AUDIT")
    print("=" * 70)

    week_picks = [
        pick
        for pick in picks
        if week_number(pick)
        == int(week)
    ]

    if not week_picks:
        print("No stored picks.")
        return

    for picker in (
        "Rico Bosco",
        "Big Cat",
        "Stool Presidente",
    ):

        picker_picks = [
            pick
            for pick in week_picks
            if picker_name(pick)
            == picker
        ]

        if not picker_picks:
            continue

        wins = sum(
            1
            for pick in picker_picks
            if str(
                pick.get("result")
                or ""
            ).upper() == "WIN"
        )

        losses = sum(
            1
            for pick in picker_picks
            if str(
                pick.get("result")
                or ""
            ).upper() == "LOSS"
        )

        pushes = sum(
            1
            for pick in picker_picks
            if str(
                pick.get("result")
                or ""
            ).upper() == "PUSH"
        )

        open_count = sum(
            1
            for pick in picker_picks
            if str(
                pick.get("result")
                or ""
            ).upper()
            not in {
                "WIN",
                "LOSS",
                "PUSH",
            }
        )

        official = sum(
            1
            for pick in picker_picks
            if is_official_result(pick)
        )

        print()
        print(picker)

        print(
            "  Record:",
            f"{wins}-{losses}-{pushes}",
            "| Open:",
            open_count,
            "| Official:",
            official,
            "| Rows:",
            len(picker_picks),
        )

        for pick in picker_picks:

            result = str(
                pick.get("result")
                or "OPEN"
            ).upper()

            event_value = (
                pick.get("event_id")
                or "-"
            )

            source = (
                "OFFICIAL"
                if is_official_result(pick)
                else "ESPN"
            )

            print(
                "   ",
                result,
                "|",
                source,
                "|",
                pick_label(pick),
                "| event:",
                event_value,
            )


# ============================================================
# MAIN GRADER
# ============================================================

def grade_open():
    picks = load_json(
        PICKS_FILE,
        [],
    )

    if not isinstance(
        picks,
        list,
    ):
        raise RuntimeError(
            "picks.json must contain a list."
        )

    print()
    print("=" * 70)
    print("GRADER PRECHECK")
    print("=" * 70)

    print(
        "Total stored wagers:",
        len(picks),
    )

    official_locked = [
        pick
        for pick in picks
        if is_official_result(pick)
    ]

    provisional = [
        pick
        for pick in picks
        if not is_official_result(pick)
    ]

    print(
        "Official result rows:",
        len(official_locked),
    )

    print(
        "Provisional ESPN rows:",
        len(provisional),
    )

    # --------------------------------------------------------
    # Normalize provisional markets and spread signs.
    # --------------------------------------------------------

    spread_lines_repaired = 0

    for pick in provisional:

        normalize_existing_pick_market(
            pick
        )

        if normalize_spread_line(
            pick
        ):
            spread_lines_repaired += 1

    print(
        "Spread lines normalized:",
        spread_lines_repaired,
    )

    # --------------------------------------------------------
    # Group CFB picks by season/week.
    # --------------------------------------------------------

    groups = {}

    for pick in provisional:

        sport = str(
            pick.get("sport")
            or ""
        ).upper()

        if sport not in {
            "CFB",
            "NCAAF",
        }:
            continue

        season_year = (
            season_year_for_pick(pick)
        )

        week = week_number(pick)

        groups.setdefault(
            (
                season_year,
                week,
            ),
            [],
        ).append(pick)

    # --------------------------------------------------------
    # ONE ESPN RESOLUTION SYSTEM.
    #
    # grade.py no longer knows how to build ESPN schedules.
    # It delegates that permanently to espn_resolver.py.
    # --------------------------------------------------------

    event_cache = {}
    failed_weeks = set()

    for (
        season_year,
        week,
    ), week_picks in sorted(
        groups.items()
    ):

        try:

            events = (
                build_complete_week_slate(
                    week_picks,
                    season_year,
                    week,
                )
            )

            event_cache[
                (
                    season_year,
                    week,
                )
            ] = events

            print(
                "Loaded SHARED ESPN schedule:",
                season_year,
                "Week",
                week,
                "| events:",
                len(events),
            )

        except Exception as exc:

            failed_weeks.add(
                (
                    season_year,
                    week,
                )
            )

            print(
                "SHARED ESPN WEEK LOAD FAILED:",
                season_year,
                "Week",
                week,
                "|",
                exc,
            )

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    official_preserved = 0
    safely_graded = 0
    unmatched = 0
    matched_not_final = 0
    review_unsupported = 0
    period_unavailable = 0
    preserved = 0
    fallback_locked = 0

    pending_event_ids = set()

    # --------------------------------------------------------
    # Grade
    # --------------------------------------------------------

    for pick in picks:

        # PAT HILL rows are immutable.
        if is_official_result(pick):
            official_preserved += 1
            continue

        sport = str(
            pick.get("sport")
            or ""
        ).upper()

        if sport not in {
            "CFB",
            "NCAAF",
        }:
            continue

        bet_type = normalize_bet_type(
            pick.get("bet_type")
        )

        pick["bet_type"] = bet_type

        if bet_type not in SUPPORTED:

            clear_grade(pick)

            mark_review(
                pick,
                (
                    "Unsupported bet type: "
                    f"{bet_type}"
                ),
            )

            review_unsupported += 1

            print(
                "UNSUPPORTED MARKET:",
                picker_name(pick),
                "|",
                pick_label(pick),
                "|",
                bet_type,
            )

            continue

        season_year = (
            season_year_for_pick(pick)
        )

        week = week_number(pick)

        cache_key = (
            season_year,
            week,
        )

        if cache_key in failed_weeks:

            preserved += 1

            print(
                "SHARED ESPN FAILURE - "
                "PRESERVING PICK:",
                picker_name(pick),
                "|",
                pick_label(pick),
            )

            continue

        events = event_cache.get(
            cache_key,
            [],
        )

        stored_event_id = (
            pick.get("event_id")
        )

        # ----------------------------------------------------
        # EXISTING PRE-GAME EVENT LOCK
        #
        # A stored event ID is authoritative for ESPN grading.
        # We NEVER rematch it to a different event.
        # ----------------------------------------------------

        if stored_event_id:

            event = find_event_by_id(
                events,
                stored_event_id,
            )

            if event is None:

                preserved += 1

                print(
                    "LOCKED EVENT MISSING FROM "
                    "SHARED SLATE - PRESERVING:",
                    picker_name(pick),
                    "|",
                    pick_label(pick),
                    "| event:",
                    stored_event_id,
                )

                continue

            print(
                "USING LOCKED EVENT:",
                picker_name(pick),
                "|",
                pick_label(pick),
                "| event:",
                stored_event_id,
            )

        # ----------------------------------------------------
        # FALLBACK
        #
        # Normally schedule_enrich.py has already locked the
        # event. If it has not, grade.py may ask the SAME shared
        # resolver for a match.
        #
        # No independent grader matching logic exists anymore.
        # ----------------------------------------------------

        else:

            resolution = (
                resolve_event_detailed(
                    pick,
                    events,
                )
            )

            event = resolution.get(
                "event"
            )

            if event is None:

                clear_grade(pick)
                mark_open(pick)

                reason = (
                    resolution.get("reason")
                    or
                    "NO_CONFIDENT_EVENT_MATCH"
                )

                pick[
                    "grading_error"
                ] = reason

                unmatched += 1

                print(
                    "NO SHARED ESPN MATCH:",
                    picker_name(pick),
                    "|",
                    pick_label(pick),
                    "| reason:",
                    reason,
                )

                continue

            event_value = str(
                event.get("id")
                or ""
            )

            if not event_value:

                clear_grade(pick)

                mark_review(
                    pick,
                    "Resolved ESPN event has no ID.",
                )

                review_unsupported += 1
                continue

            pick[
                "event_id"
            ] = event_value

            pick[
                "game_match_status"
            ] = "MATCHED"

            pick[
                "game_match_source"
            ] = "ESPN"

            pick[
                "game_match_method"
            ] = (
                resolution.get("method")
                or "SHARED_RESOLVER"
            )

            pick[
                "game_match_confidence"
            ] = (
                resolution.get("confidence")
                or "SHARED_RESOLVER"
            )

            fallback_locked += 1

            print(
                "SHARED RESOLVER FALLBACK LOCK:",
                picker_name(pick),
                "|",
                pick_label(pick),
                "| method:",
                resolution.get("method"),
                "| event:",
                event_value,
            )

        # ----------------------------------------------------
        # Refresh harmless ESPN metadata.
        # ----------------------------------------------------

        clear_grade(pick)

        refresh_event_metadata(
            pick,
            event,
        )

        event_value = str(
            event.get("id")
            or ""
        )

        # ----------------------------------------------------
        # OPEN GAME
        # ----------------------------------------------------

        if not completed(event):

            mark_open(pick)

            matched_not_final += 1

            if event_value:
                pending_event_ids.add(
                    event_value
                )

            print(
                "MATCHED - NOT FINAL:",
                picker_name(pick),
                "|",
                pick_label(pick),
                "| event:",
                event_value,
            )

            continue

        # ----------------------------------------------------
        # FINAL GAME:
        # hydrate exact event from ESPN summary.
        # ----------------------------------------------------

        if event_value:

            try:

                summary = fetch_summary(
                    event_value
                )

                event = (
                    hydrate_event_from_summary(
                        event,
                        summary,
                    )
                )

            except Exception as exc:

                print(
                    "SUMMARY FETCH FAILED:",
                    event_value,
                    "|",
                    exc,
                )

        # Fail closed if ESPN still does not confirm completion.
        if not completed(event):

            mark_open(pick)

            matched_not_final += 1

            if event_value:
                pending_event_ids.add(
                    event_value
                )

            continue

        # ----------------------------------------------------
        # PERIOD MARKETS
        # ----------------------------------------------------

        period = market_period(
            bet_type
        )

        period_scores = None

        if period in {
            "FIRST_QUARTER",
            "FIRST_HALF",
        }:

            try:

                period_scores = (
                    event_period_scores(
                        event,
                        period,
                    )
                )

            except Exception as exc:

                period_scores = None

                print(
                    "PERIOD SCORE ERROR:",
                    event_value,
                    "|",
                    period,
                    "|",
                    exc,
                )

            if not period_scores:

                mark_open(pick)

                pick[
                    "grading_error"
                ] = (
                    "Period score unavailable"
                )

                period_unavailable += 1

                print(
                    "PERIOD SCORE UNAVAILABLE:",
                    picker_name(pick),
                    "|",
                    pick_label(pick),
                    "| event:",
                    event_value,
                )

                continue

        # ----------------------------------------------------
        # GRADE
        # ----------------------------------------------------

        result = grade_market(
            pick,
            event,
            period_scores,
        )

        if result not in {
            "WIN",
            "LOSS",
            "PUSH",
        }:

            mark_review(
                pick,
                (
                    "Could not safely grade "
                    "locked ESPN event."
                ),
            )

            review_unsupported += 1

            print(
                "MATCHED BUT NOT GRADEABLE:",
                picker_name(pick),
                "|",
                pick_label(pick),
                "| event:",
                event_value,
            )

            continue

        apply_grade(
            pick,
            event,
            result,
        )

        if period_scores is not None:
            pick[
                "period_score"
            ] = period_scores

        safely_graded += 1

        print(
            "GRADED:",
            picker_name(pick),
            "|",
            pick_label(pick),
            "|",
            result,
            "|",
            final_score_text(event),
            "| event:",
            event_value,
        )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_json(
        PICKS_FILE,
        picks,
    )

    print()
    print("=" * 70)
    print("GRADING SUMMARY")
    print("=" * 70)

    print(
        "Spread lines normalized:",
        spread_lines_repaired,
    )

    print(
        "Official results preserved:",
        official_preserved,
    )

    print(
        "Safely ESPN graded:",
        safely_graded,
    )

    print(
        "Unmatched supported picks:",
        unmatched,
    )

    print(
        "Matched but not final picks:",
        matched_not_final,
    )

    print(
        "Review / unsupported:",
        review_unsupported,
    )

    print(
        "Period score unavailable:",
        period_unavailable,
    )

    print(
        "Preserved due ESPN failure:",
        preserved,
    )

    print(
        "Shared-resolver fallback locks:",
        fallback_locked,
    )

    print(
        "Unique pending tracked games:",
        len(pending_event_ids),
    )

    weeks = sorted(
        {
            week_number(pick)
            for pick in picks
        }
    )

    for week in weeks:
        print_week_audit(
            picks,
            week,
        )

    return picks


# ============================================================
# DIRECT RUN
# ============================================================

if __name__ == "__main__":
    grade_open()
