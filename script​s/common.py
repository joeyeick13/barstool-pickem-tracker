from __future__ import annotations
import json, os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PICKS_FILE = DATA / "picks.json"
STATE_FILE = DATA / "state.json"
PICKERS = ["Big Cat", "Stool Presidente", "Rico Bosco"]


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_picker(name: str | None):
    if not name:
        return None
    n = name.lower().strip()
    if "big cat" in n or "bigcat" in n:
        return "Big Cat"
    if any(x in n for x in ["presidente", "portnoy", "el pres", "stool pres"]):
        return "Stool Presidente"
    if "rico" in n or "bosco" in n:
        return "Rico Bosco"
    return None


def american_profit(odds: int | float | None, risk_units: float, result: str) -> float:
    if result == "LOSS":
        return -risk_units
    if result in {"PUSH", "VOID", "OPEN", "LATE", "REVIEW"}:
        return 0.0
    if not odds:
        odds = -110
    odds = float(odds)
    if odds > 0:
        return round(risk_units * odds / 100.0, 3)
    return round(risk_units * 100.0 / abs(odds), 3)
