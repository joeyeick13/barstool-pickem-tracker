from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests

from common import PICKS_FILE, load_json, save_json
from football_identity import (
    base_market,
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

# This replacement is intentionally observation-only until one live run proves
# ESPN's 2026 CFB DraftKings payload against the real Week 4 card.
READ_ONLY = True


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
