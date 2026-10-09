from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests

from common import PICKS_FILE, load_json, save_json
from football_identity import (
    base_market,
    canonical_team,
    market_period,
    normalize_bet_type,
    safe_float,
    side_identity,
    teams_equivalent,
)
from espn_resolver import (
    build_complete_week_slate,
    competitor_display_name,
    competitor_home_away,
    competitors,
    event_id,
    event_matchup_text,
    resolve_event_detailed,
)


# ============================================================
# CONFIG
# ============================================================

ESPN_ODDS_BASE = (
    "https://sports.core.api.espn.com/v2/sports/football/"
    "leagues/college-football"
)
DRAFTKINGS_PROVIDER_IDS = {"100", "41"}
DRAFTKINGS_PROVIDER_NAME = "draftkings"
HTTP_TIMEOUT = 30

# Keep the already-official week available for read-only historical audit.
AUDIT_OFFICIAL_WEEK = int(os.getenv("SPORTSBOOK_AUDIT_WEEK", "4"))
SEASON_YEAR = int(os.getenv("PICKEM_SEASON_YEAR", "2026"))

# Official result rows may receive a tightly constrained sign-only correction
# when ESPN/DraftKings proves the opposite sign within the agreed 3-point band.


# ============================================================
# CONTEXT-ONLY TEAM ABBREVIATIONS
# ============================================================
# These are NEVER registered as global football aliases. They are only used
# inside an already-known two-team ESPN event, or as a last-resort two-team
# matchup resolver that must produce exactly one ESPN event.

_CONTEXT_ALIASES = {
    "osu": ("ohio state", "oklahoma state", "oregon state"),
    "um": ("michigan", "miami", "mississippi", "montana"),
    "usc": ("usc", "south carolina"),
    "tu": ("temple", "tulane", "tulsa"),
    # Reconciled Week 4 source text has "OU @ USC" for Oregon @ USC.
    # Keeping OU contextual prevents a global Oklahoma/Oregon ambiguity.
    "ou": ("oklahoma", "oregon"),
}


# ============================================================
# BASIC HELPERS
# ============================================================

def _clean(value):
    return str(value or "").strip()


def _norm_token(value):
    return "".join(ch for ch in _clean(value).lower() if ch.isalnum())


def _week_number(pick):
    try:
        return int(pick.get("week"))
    except (TypeError, ValueError):
        return None


def _is_cfb(pick):
    return _clean(pick.get("sport")).upper() in {"CFB", "NCAAF"}


def _is_official(pick):
    return bool(pick.get("official_reconciled"))


def _eligible_for_validation(pick):
    if not _is_cfb(pick):
        return False

    week = _week_number(pick)

    # Read-only historical audit for the reconciled week.
    if week == AUDIT_OFFICIAL_WEEK:
        return True

    # Every other week: only provisional picks.
    return not _is_official(pick)


def _label(pick):
    return f"W{pick.get('week')} | {pick.get('picker')} | {pick.get('selection')}"


def _same_team(left, right):
    if not left or not right:
        return False
    try:
        return bool(teams_equivalent(left, right))
    except Exception:
        return False


def _split_matchup(value):
    text = _clean(value)
    lower = text.lower()

    for separator in (" @ ", " vs. ", " vs ", " v "):
        index = lower.find(separator)
        if index < 0:
            continue
        left = text[:index].strip()
        right = text[index + len(separator):].strip()
        if left and right:
            return left, right

    return None, None


def _pick_matchup(pick):
    return (
        _clean(pick.get("event_matchup"))
        or _clean(pick.get("matchup"))
        or _clean(pick.get("game_matchup"))
        or _clean(pick.get("source_matchup_text"))
    )


# ============================================================
# ESPN EVENT IDENTITY
# ============================================================

def _event_team_names(event):
    return [
        competitor_display_name(comp)
        for comp in competitors(event)
        if competitor_display_name(comp)
    ]


def _event_sides(event):
    home = None
    away = None

    for comp in competitors(event):
        name = competitor_display_name(comp)
        side = competitor_home_away(comp)
        if side == "home":
            home = name
        elif side == "away":
            away = name

    return home, away


def _context_hint_matches_team(hint, team_name):
    if _same_team(hint, team_name):
        return True

    return any(
        _same_team(candidate, team_name)
        for candidate in _CONTEXT_ALIASES.get(_norm_token(hint), ())
    )


def _event_matches_context_pair(event, first, second):
    names = _event_team_names(event)
    if len(names) != 2:
        return False

    first_indexes = [
        i for i, name in enumerate(names)
        if _context_hint_matches_team(first, name)
    ]
    second_indexes = [
        i for i, name in enumerate(names)
        if _context_hint_matches_team(second, name)
    ]

    return any(i != j for i in first_indexes for j in second_indexes)


def _resolve_event_for_validation(pick, events):
    """Permanent resolver first; tightly constrained two-team fallback second."""

    resolver_pick = dict(pick)

    # Official reconciliation removed the original pregame lock. For the audit,
    # intentionally reconstruct it. Provisional rows keep their real event_id.
    if _is_official(pick):
        resolver_pick["event_id"] = None

    resolved = resolve_event_detailed(resolver_pick, events)
    if resolved.get("event") is not None:
        return resolved

    # Never bypass a missing/stale stored event_id on a provisional row.
    if not _is_official(pick) and _clean(pick.get("event_id")):
        return resolved

    first, second = _split_matchup(_pick_matchup(pick))
    if not first or not second:
        return resolved

    candidates = [
        event for event in events
        if _event_matches_context_pair(event, first, second)
    ]

    if len(candidates) != 1:
        return resolved

    event = candidates[0]
    return {
        "event": event,
        "event_id": event_id(event),
        "method": "SPORTSBOOK_CONTEXT_PAIR",
        "reason": "Unique contextual two-team ESPN matchup matched.",
        "confidence": 1.0,
        "candidate_event_ids": [event_id(event)],
    }


def _selected_espn_team(pick, event):
    """Resolve an ambiguous selection only inside the exact ESPN event."""

    selected = _clean(side_identity(pick) or pick.get("team") or pick.get("side"))
    names = _event_team_names(event)

    if not selected or len(names) != 2:
        return None, "NO_SELECTED_TEAM"

    direct = [name for name in names if _same_team(selected, name)]
    if len(direct) == 1:
        return direct[0], "DIRECT_IDENTITY"

    key = _norm_token(selected)

    # Verified source-card rule: Texas @ Tennessee + "UT +4.5" = Tennessee.
    # This remains event-local and never becomes a global UT alias.
    if key == "ut":
        texas_present = any(_same_team("texas", name) for name in names)
        tennessee = [name for name in names if _same_team("tennessee", name)]
        if texas_present and len(tennessee) == 1:
            return tennessee[0], "CONTEXT_UT_TEXAS_TENNESSEE"

    contextual = [
        name for name in names
        if any(
            _same_team(candidate, name)
            for candidate in _CONTEXT_ALIASES.get(key, ())
        )
    ]

    if len(contextual) == 1:
        return contextual[0], f"CONTEXT_{key.upper()}"

    return None, "AMBIGUOUS_SELECTED_TEAM"


def _selected_home_away(selected_team, event):
    home, away = _event_sides(event)

    home_match = _same_team(selected_team, home)
    away_match = _same_team(selected_team, away)

    if home_match and not away_match:
        return "home"
    if away_match and not home_match:
        return "away"
    return None


# ============================================================
# ESPN ODDS CLIENT
# ============================================================

class EspnOdds:
    def __init__(self):
        self.session = requests.Session()
        self.cache = {}

    def event_odds(self, event_id_value):
        event_id_value = str(event_id_value)
        if event_id_value in self.cache:
            return self.cache[event_id_value]

        url = (
            f"{ESPN_ODDS_BASE}/events/{event_id_value}/"
            f"competitions/{event_id_value}/odds"
        )

        response = self.session.get(
            url,
            params={"lang": "en", "region": "us"},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.espn.com",
                "Referer": "https://www.espn.com/",
            },
            timeout=HTTP_TIMEOUT,
        )

        if response.status_code == 404:
            payload = {}
        else:
            response.raise_for_status()
            payload = response.json()

        self.cache[event_id_value] = payload if isinstance(payload, dict) else {}
        return self.cache[event_id_value]


# ============================================================
# DRAFTKINGS PROVIDER
# ============================================================

def _provider_id(item):
    return _clean((item.get("provider") or {}).get("id"))


def _provider_name(item):
    return _clean((item.get("provider") or {}).get("name"))


def _available_providers(payload):
    rows = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": _provider_id(item),
                "name": _provider_name(item),
            }
        )
    return rows


def _draftkings_item(payload):
    items = [
        item
        for item in (payload.get("items") or [])
        if isinstance(item, dict)
    ]

    # Provider IDs have changed across ESPN feeds.  The live 2026 CFB
    # payload returned DraftKings as provider 100, while older public ESPN
    # examples use 41.  Provider name is therefore authoritative here.
    exact_name = [
        item
        for item in items
        if _norm_token(_provider_name(item)) == DRAFTKINGS_PROVIDER_NAME
    ]
    if len(exact_name) == 1:
        return exact_name[0]

    # Fail-safe fallback for payloads that omit provider.name.
    exact_id = [
        item
        for item in items
        if _provider_id(item) in DRAFTKINGS_PROVIDER_IDS
    ]
    if len(exact_id) == 1:
        return exact_id[0]

    return None


# ============================================================
# ESPN ODDS VALUE EXTRACTION
# ============================================================

def _number_from_market(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, dict):
        return None

    for key in ("value", "line", "handicap"):
        number = safe_float(value.get(key))
        if number is not None:
            return number
    return None


def _american_from_market(value):
    if isinstance(value, (int, float, str)):
        return safe_float(value)
    if not isinstance(value, dict):
        return None

    for key in ("american", "value"):
        number = safe_float(value.get(key))
        if number is not None:
            return number
    return None


def _snapshot_order(pick):
    # Reconciled historical rows lost their original X timestamp, so close is
    # the audit proxy. Provisional rows are checked immediately after ingest,
    # so current is the useful real-time comparison.
    if _is_official(pick):
        return ("close", "current", "summary", "open")
    return ("current", "summary", "open", "close")


def _team_odds_block(item, side):
    if side == "home":
        return item.get("homeTeamOdds") or {}
    if side == "away":
        return item.get("awayTeamOdds") or {}
    return {}


def _spread_for_selected_team(item, pick, event, selected_team):
    """Return the selected team point spread from ESPN's top-level line.

    ESPN's top-level ``spread`` is the POINT SPREAD and is expressed from
    the home team's perspective.  Nested ``spreadOdds`` / snapshot ``value``
    fields are prices (for example 1.90 decimal), not point spreads.
    """

    side = _selected_home_away(selected_team, event)
    if side is None:
        return None, None

    home_spread = safe_float(item.get("spread"))
    if home_spread is None:
        return None, None

    selected_line = (
        home_spread
        if side == "home"
        else -home_spread
    )

    return selected_line, "summary"


def _total_line(item, pick):
    """Return ESPN's game total.

    ``overUnder`` is the points total.  Nested over/under odds are prices and
    must never be treated as the total itself.
    """

    number = safe_float(item.get("overUnder"))
    if number is None:
        return None, None

    return number, "summary"


def _moneyline_for_selected_team(item, pick, event, selected_team):
    side = _selected_home_away(selected_team, event)
    if side is None:
        return None, None

    team_odds = _team_odds_block(item, side)

    for snapshot in _snapshot_order(pick):
        if snapshot == "summary":
            number = safe_float(team_odds.get("moneyLine"))
            if number is not None:
                return number, "summary"
            continue

        block = team_odds.get(snapshot)
        if not isinstance(block, dict):
            continue

        number = _american_from_market(block.get("moneyLine"))
        if number is not None:
            return number, snapshot

    return None, None


# ============================================================
# MARKET SUPPORT
# ============================================================

def _market_family(pick):
    return base_market(normalize_bet_type(pick.get("bet_type")))


def _full_game_period(pick):
    period = _clean(
        market_period(normalize_bet_type(pick.get("bet_type")))
    ).lower()

    return period in {"", "full", "fullgame", "full_game", "game"}


# ============================================================
# AUDIT RULES
# ============================================================

def _spread_audit(item, pick, event):
    if not _full_game_period(pick):
        return "UNAVAILABLE", "ESPN_MAIN_ODDS_DO_NOT_PROVE_THIS_PERIOD", None

    x_line = safe_float(pick.get("line"))
    if x_line is None:
        return "REVIEW", "MISSING_X_SPREAD_LINE", None

    selected_team, identity_method = _selected_espn_team(pick, event)
    if not selected_team:
        return "REVIEW", identity_method, None

    dk_line, snapshot = _spread_for_selected_team(
        item,
        pick,
        event,
        selected_team,
    )

    if dk_line is None:
        return (
            "UNAVAILABLE",
            "NO_DRAFTKINGS_SPREAD_LINE",
            {
                "selected_espn_team": selected_team,
                "identity_method": identity_method,
            },
        )

    magnitude_difference = abs(abs(x_line) - abs(dk_line))
    exact = abs(x_line - dk_line) <= 0.001
    same_sign = exact or (x_line == 0 and dk_line == 0) or (x_line * dk_line > 0)

    detail = {
        "provider_id": _provider_id(item),
        "provider_name": _provider_name(item),
        "selected_espn_team": selected_team,
        "identity_method": identity_method,
        "x_line": x_line,
        "draftkings_line": dk_line,
        "snapshot": snapshot,
        "magnitude_difference": round(magnitude_difference, 3),
    }

    if exact:
        return "CONFIRMED", "EXACT_DRAFTKINGS_SPREAD_MATCH", detail
    if same_sign:
        return "NO_CHANGE", "SAME_SIGN_DRAFTKINGS_LINE_MOVEMENT", detail
    if magnitude_difference <= 3.0:
        return "SIGN_REVIEW", "OPPOSITE_SIGN_WITHIN_3_POINTS", detail
    return "REVIEW", "OPPOSITE_SIGN_OVER_3_POINTS", detail


def _total_audit(item, pick):
    if not _full_game_period(pick):
        return "UNAVAILABLE", "ESPN_MAIN_ODDS_DO_NOT_PROVE_THIS_PERIOD", None

    x_line = safe_float(pick.get("line"))
    if x_line is None:
        return "REVIEW", "MISSING_X_TOTAL_LINE", None

    dk_line, snapshot = _total_line(item, pick)
    if dk_line is None:
        return "UNAVAILABLE", "NO_DRAFTKINGS_TOTAL_LINE", None

    detail = {
        "provider_id": _provider_id(item),
        "provider_name": _provider_name(item),
        "x_line": x_line,
        "draftkings_total": dk_line,
        "snapshot": snapshot,
    }

    if abs(x_line - dk_line) <= 0.001:
        return "CONFIRMED", "EXACT_DRAFTKINGS_TOTAL_MATCH", detail

    # Totals are observation-only. Line movement never proves over/under side
    # extraction was wrong.
    return "NO_CHANGE", "DRAFTKINGS_TOTAL_MOVED", detail


def _moneyline_audit(item, pick, event):
    if not _full_game_period(pick):
        return "UNAVAILABLE", "ESPN_MAIN_ODDS_DO_NOT_PROVE_THIS_PERIOD", None

    selected_team, identity_method = _selected_espn_team(pick, event)
    if not selected_team:
        return "REVIEW", identity_method, None

    price, snapshot = _moneyline_for_selected_team(
        item,
        pick,
        event,
        selected_team,
    )

    if price is None:
        return (
            "UNAVAILABLE",
            "NO_DRAFTKINGS_MONEYLINE",
            {
                "selected_espn_team": selected_team,
                "identity_method": identity_method,
            },
        )

    return (
        "CONFIRMED",
        "SELECTED_TEAM_PRESENT_IN_DRAFTKINGS_MONEYLINE",
        {
            "provider_id": _provider_id(item),
            "provider_name": _provider_name(item),
            "selected_espn_team": selected_team,
            "identity_method": identity_method,
            "draftkings_moneyline": price,
            "snapshot": snapshot,
        },
    )


def _audit_pick(item, pick, event):
    family = _market_family(pick)

    if family == "SPREAD":
        return _spread_audit(item, pick, event)
    if family == "TOTAL":
        return _total_audit(item, pick)
    if family == "MONEYLINE":
        return _moneyline_audit(item, pick, event)
    if family == "TEAM_TOTAL":
        return "UNAVAILABLE", "ESPN_MAIN_ODDS_DO_NOT_PROVE_TEAM_TOTAL", None

    return "UNAVAILABLE", f"UNSUPPORTED_MARKET_{family}", None


# ============================================================
# SAFE SPORTSBOOK SPREAD SIGN CORRECTION
# ============================================================

def _format_line(value):
    number = float(value)
    if abs(number - round(number)) <= 0.000001:
        return f"{number:+.0f}"
    return f"{number:+g}"


def _replace_selection_spread(selection, old_line, new_line):
    """Replace only the trailing spread number in a selection string."""

    text = _clean(selection)
    if not text:
        return text

    replacement = _format_line(new_line)

    # Pick selections are stored in forms such as:
    #   Oregon -3
    #   MINN -9.5
    #   Kentucky +20.5
    # Replace only a final signed numeric token and preserve the team text.
    updated, count = re.subn(
        r"([+-])\s*\d+(?:\.\d+)?\s*$",
        replacement,
        text,
        count=1,
    )

    if count == 1:
        return updated

    # Fail closed rather than manufacturing a new selection format.
    return text


def _apply_spread_sign_correction(pick, event, status, detail):
    """Apply the agreed DraftKings sign correction.

    Rules:
      * only opposite-sign spread discrepancies are eligible
      * DraftKings must be within 3 points in absolute magnitude
      * preserve the SOURCE magnitude and change only the sign
      * Kentucky +20.5 vs DK -20.5 -> Kentucky -20.5
      * Minnesota -9.5 vs DK +12.5 -> Minnesota +9.5
      * provisional rows require their existing exact ESPN event lock
      * official audit rows may use the unique ESPN event reconstructed here
      * preserve the original selection / line / official result once
      * corrected official rows are marked for one ESPN re-grade downstream
      * idempotent on rerun
    """

    if status != "SIGN_REVIEW":
        return False

    if not isinstance(detail, dict):
        return False

    resolved_event_id = _clean(event_id(event))
    if not resolved_event_id:
        return False

    is_official = _is_official(pick)
    stored_event_id = _clean(pick.get("event_id"))

    # A provisional pick must already have been locked pre-game to this exact
    # ESPN event.  We never let sportsbook validation create/rematch that lock.
    if not is_official:
        if (
            not stored_event_id
            or stored_event_id != resolved_event_id
        ):
            return False

    old_line = safe_float(pick.get("line"))
    dk_line = safe_float(detail.get("draftkings_line"))

    if old_line is None or dk_line is None:
        return False

    # The sides must genuinely disagree.
    if old_line == 0 or dk_line == 0 or old_line * dk_line >= 0:
        return False

    magnitude_difference = abs(abs(old_line) - abs(dk_line))
    if magnitude_difference > 3.0 + 0.000001:
        return False

    # IMPORTANT: DraftKings is used to validate the SIGN, not to replace the
    # source number.  The source magnitude remains authoritative.
    corrected_line = (
        abs(old_line)
        if dk_line > 0
        else -abs(old_line)
    )

    old_selection = _clean(pick.get("selection"))
    new_selection = _replace_selection_spread(
        old_selection,
        old_line,
        corrected_line,
    )

    # If we cannot safely update the visible selection, do not update the
    # numeric line either. This preserves audit_tracker spread integrity.
    if new_selection == old_selection:
        return False

    if "pre_sportsbook_selection" not in pick:
        pick["pre_sportsbook_selection"] = old_selection

    if "pre_sportsbook_line" not in pick:
        pick["pre_sportsbook_line"] = old_line

    if is_official:
        if "pre_sportsbook_result" not in pick:
            pick["pre_sportsbook_result"] = pick.get("result")

        if "pre_sportsbook_status" not in pick:
            pick["pre_sportsbook_status"] = pick.get("status")

        if "pre_sportsbook_profit" not in pick:
            pick["pre_sportsbook_profit"] = pick.get("profit")

        if "pre_sportsbook_grade_source" not in pick:
            pick["pre_sportsbook_grade_source"] = pick.get("grade_source")

    pick["selection"] = new_selection
    pick["line"] = corrected_line

    # Official reconciled rows historically have no ESPN lock.  The
    # sportsbook audit has just uniquely reconstructed this exact event, so
    # persist it for the one downstream re-grade.  Provisional rows already
    # have this same ID and are left logically unchanged.
    pick["event_id"] = resolved_event_id

    pick["sportsbook_corrected"] = True
    pick["sportsbook_correction_type"] = (
        "SIGN_ONLY_PRESERVE_SOURCE_MAGNITUDE"
    )
    pick["sportsbook_correction_source"] = "ESPN_DRAFTKINGS"
    pick["sportsbook_correction_event_id"] = resolved_event_id
    pick["sportsbook_reference_line"] = dk_line
    pick["sportsbook_magnitude_difference"] = round(
        magnitude_difference,
        3,
    )
    pick["sportsbook_correction_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    if is_official:
        pick["sportsbook_regrade_required"] = True
        pick["sportsbook_regraded"] = False

    return True



# ============================================================
# POST-CORRECTION PROVISIONAL DEDUPE
# ============================================================

def _source_evidence(pick):
    evidence = {}

    raw = pick.get("source_post_evidence")
    if isinstance(raw, dict):
        for raw_id, raw_meta in raw.items():
            post_id = str(raw_id or "").strip()
            if not post_id:
                continue
            evidence[post_id] = dict(raw_meta) if isinstance(raw_meta, dict) else {}

    primary_id = _clean(pick.get("source_post_id"))
    if primary_id and primary_id not in evidence:
        evidence[primary_id] = {
            "url": pick.get("source_url"),
            "text": pick.get("source_text"),
            "is_reply": pick.get("source_is_reply"),
            "conversation_id": pick.get("conversation_id"),
            "posted_at": pick.get("posted_at"),
        }

    for raw_id in (pick.get("source_post_ids") or []):
        post_id = str(raw_id or "").strip()
        if post_id:
            evidence.setdefault(post_id, {})

    return evidence


def _merge_duplicate_provenance(keeper, duplicate):
    evidence = _source_evidence(keeper)
    evidence.update(_source_evidence(duplicate))

    if evidence:
        keeper["source_post_evidence"] = evidence
        keeper["source_post_ids"] = sorted(
            evidence,
            key=lambda value: int(value) if str(value).isdigit() else 0,
        )

    audit_row = {
        "selection": duplicate.get("selection"),
        "line": duplicate.get("line"),
        "pre_sportsbook_selection": duplicate.get("pre_sportsbook_selection"),
        "pre_sportsbook_line": duplicate.get("pre_sportsbook_line"),
        "source_post_id": duplicate.get("source_post_id"),
        "event_id": duplicate.get("event_id"),
        "correction_type": duplicate.get("sportsbook_correction_type"),
    }

    history = keeper.setdefault(
        "merged_duplicate_audit",
        [],
    )
    if audit_row not in history:
        history.append(audit_row)


def _provisional_spread_identity(pick):
    if _is_official(pick) or not _is_cfb(pick):
        return None

    bet_type = normalize_bet_type(pick.get("bet_type"))
    if base_market(bet_type) != "SPREAD":
        return None

    current_event_id = _clean(pick.get("event_id"))
    if not current_event_id:
        return None

    line = safe_float(pick.get("line"))
    if line is None:
        return None

    selected = canonical_team(side_identity(pick))
    if not selected:
        return None

    return (
        _clean(pick.get("picker")).lower(),
        _week_number(pick),
        market_period(bet_type),
        current_event_id,
        selected,
        round(float(line), 4),
    )


def _dedupe_provisional_spreads_after_sign_validation(picks):
    """Remove exact provisional spread duplicates created/exposed by sign fixes.

    This is intentionally downstream of DraftKings validation.  It does not
    guess which sign is right.  It only merges rows after both wagers already
    resolve to the same picker/week/period/ESPN event/selected team/exact line.
    """
    groups = defaultdict(list)

    for index, pick in enumerate(picks):
        if not isinstance(pick, dict):
            continue
        key = _provisional_spread_identity(pick)
        if key is not None:
            groups[key].append((index, pick))

    remove_indexes = set()
    removed = 0

    for key, members in groups.items():
        if len(members) < 2:
            continue

        # Prefer a row that was already source-correct and/or already graded.
        # A newly sportsbook-corrected duplicate should not displace the older
        # clean row simply because validation exposed their equality.
        def keeper_score(item):
            index, pick = item
            graded = 1 if pick.get("result") in {"WIN", "LOSS", "PUSH"} else 0
            uncorrected = 1 if not pick.get("sportsbook_corrected") else 0
            has_matchup = 1 if _pick_matchup(pick) else 0
            return (graded, uncorrected, has_matchup, -index)

        keeper_index, keeper = max(members, key=keeper_score)

        for index, duplicate in members:
            if index == keeper_index:
                continue

            _merge_duplicate_provenance(keeper, duplicate)
            remove_indexes.add(index)
            removed += 1

            print(
                "POST-CORRECTION DUPLICATE MERGED:",
                _label(duplicate),
                "| event:",
                duplicate.get("event_id"),
                "| kept:",
                _label(keeper),
            )

    if not remove_indexes:
        return picks, 0

    cleaned = [
        pick
        for index, pick in enumerate(picks)
        if index not in remove_indexes
    ]

    return cleaned, removed

# ============================================================
# MAIN
# ============================================================

def validate_sportsbook():
    print()
    print("=" * 72)
    print("DRAFTKINGS VALIDATION — ESPN EVENT + ESPN ODDS")
    print("=" * 72)
    print(f"Historical official audit week: {AUDIT_OFFICIAL_WEEK}")
    print("DraftKings provider: matched by provider name")
    print("Safe spread sign corrections are enabled for provisional and official audit rows.")

    picks = load_json(PICKS_FILE, [])
    if not isinstance(picks, list):
        raise RuntimeError("picks.json must contain a list.")

    eligible = [
        pick for pick in picks
        if isinstance(pick, dict) and _eligible_for_validation(pick)
    ]

    if not eligible:
        print("No eligible CFB wagers found.")
        return

    # Build exactly one complete ESPN slate per eligible Pick Em week.
    by_week = defaultdict(list)
    for pick in eligible:
        week = _week_number(pick)
        if week is not None:
            by_week[week].append(pick)

    events_by_week = {}
    for week, week_picks in sorted(by_week.items()):
        events_by_week[week] = build_complete_week_slate(
            week_picks,
            SEASON_YEAR,
            week,
        )

    odds_client = EspnOdds()
    counts = Counter()
    payload_cache = {}
    resolved_event_ids = set()
    draftkings_event_ids = set()
    corrections_saved = 0

    print()
    print("-" * 72)
    print("WAGER AUDIT")
    print("-" * 72)

    for pick in eligible:
        label = _label(pick)
        week = _week_number(pick)
        events = events_by_week.get(week, [])

        resolution = _resolve_event_for_validation(pick, events)
        event = resolution.get("event")

        if event is None:
            status = "ESPN_UNRESOLVED"
            counts[status] += 1
            print(
                f"{status}: {label} | "
                f"{resolution.get('method')} | {resolution.get('reason')}"
            )
            continue

        current_event_id = event_id(event)
        resolved_event_ids.add(current_event_id)

        if current_event_id not in payload_cache:
            try:
                payload_cache[current_event_id] = odds_client.event_odds(current_event_id)
            except requests.HTTPError as exc:
                response_status = getattr(exc.response, "status_code", "unknown")
                payload_cache[current_event_id] = {
                    "_api_error": f"HTTP_{response_status}"
                }
            except requests.RequestException as exc:
                payload_cache[current_event_id] = {
                    "_api_error": f"{type(exc).__name__}: {exc}"
                }

        payload = payload_cache[current_event_id]

        if payload.get("_api_error"):
            status = "API_ERROR"
            counts[status] += 1
            print(
                f"{status}: {label} | ESPN {current_event_id} | "
                f"{payload['_api_error']}"
            )
            continue

        item = _draftkings_item(payload)

        if item is None:
            status = "NO_DRAFTKINGS_PROVIDER"
            counts[status] += 1
            print(
                f"{status}: {label} | ESPN {current_event_id} | "
                f"{event_matchup_text(event)}"
            )
            print("  available_providers:", _available_providers(payload))
            continue

        draftkings_event_ids.add(current_event_id)

        try:
            status, reason, detail = _audit_pick(item, pick, event)
        except Exception as exc:
            status = "VALIDATOR_ERROR"
            reason = f"{type(exc).__name__}: {exc}"
            detail = None

        if _apply_spread_sign_correction(
            pick,
            event,
            status,
            detail,
        ):
            corrections_saved += 1
            status = "CORRECTED"
            reason = "OPPOSITE_SIGN_WITHIN_3_POINTS_SIGN_ONLY_APPLIED"
            label = _label(pick)

        counts[status] += 1
        print(
            f"{status}: {label} | ESPN {current_event_id} | {reason}"
        )
        if detail:
            print("  ", detail)

    picks, post_correction_duplicates_removed = (
        _dedupe_provisional_spreads_after_sign_validation(
            picks
        )
    )

    print()
    print("-" * 72)
    print("ESPN DRAFTKINGS AUDIT SUMMARY")
    print("-" * 72)
    print(f"Eligible CFB wagers: {len(eligible)}")
    print(f"Unique ESPN events resolved: {len(resolved_event_ids)}")
    print(
        "Unique ESPN events with DraftKings odds: "
        f"{len(draftkings_event_ids)}"
    )
    if corrections_saved or post_correction_duplicates_removed:
        save_json(
            PICKS_FILE,
            picks,
        )

    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    print("-" * 72)
    print(f"Sportsbook sign corrections saved: {corrections_saved}")
    print(
        "Post-correction provisional duplicates removed: "
        f"{post_correction_duplicates_removed}"
    )


if __name__ == "__main__":
    validate_sportsbook()
