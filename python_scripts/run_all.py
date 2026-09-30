import os

from x_ingest import ingest
from data_corrections import apply_verified_corrections
from total_matchup_enrich import enrich_total_matchups
from schedule_enrich import enrich_schedule
from sportsbook_validate import validate_sportsbook
from grade import grade_open
from audit_tracker import run_audit
from build_site import build


def secrets_configured():
    return bool(
        os.getenv("X_BEARER_TOKEN")
        and os.getenv("OPENAI_API_KEY")
    )


def run_pipeline():
    print()
    print("=" * 72)
    print("BARSTOOL PICK EM TRACKER")
    print("PERMANENT PIPELINE")
    print("=" * 72)

    have_secrets = secrets_configured()

    # ========================================================
    # STEP 1 — X INGESTION
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 1 — X INGESTION")
    print("=" * 72)

    if have_secrets:
        ingest()
    else:
        print(
            "Skipping X ingestion: "
            "X/OpenAI secrets not configured."
        )

    # ========================================================
    # STEP 2 — HISTORICAL VERIFIED CORRECTIONS
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 2 — HISTORICAL VERIFIED CORRECTIONS")
    print("=" * 72)

    apply_verified_corrections()

    # ========================================================
    # STEP 3 — LEGACY TOTAL MATCHUP ENRICHMENT
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 3 — LEGACY TOTAL MATCHUP ENRICHMENT")
    print("=" * 72)

    if have_secrets:
        enrich_total_matchups()
    else:
        print(
            "Skipping total matchup verification: "
            "X/OpenAI secrets not configured."
        )

    # ========================================================
    # STEP 4 — ESPN EVENT LOCKING
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 4 — ESPN EVENT LOCKING")
    print("=" * 72)

    enrich_schedule()

    # ========================================================
    # STEP 5 — DRAFTKINGS VALIDATION
    # ========================================================
    #
    # sportsbook_validate.py independently checks the
    # ESPN-locked CFB wager against DraftKings via OddsPapi.
    #
    # The initial implementation runs in observation mode:
    # it reports sportsbook comparisons but does NOT modify
    # picks.json.
    #
    # Official PAT HILL reconciled rows remain authoritative
    # and are excluded from sportsbook validation.
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 5 — DRAFTKINGS VALIDATION")
    print("=" * 72)

    validate_sportsbook()

    # ========================================================
    # STEP 6 — GRADING
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 6 — GRADING")
    print("=" * 72)

    grade_open()

    # ========================================================
    # STEP 7 — PERMANENT INTEGRITY AUDIT
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 7 — TRACKER INTEGRITY AUDIT")
    print("=" * 72)

    run_audit()

    # ========================================================
    # STEP 8 — DASHBOARD BUILD
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 8 — DASHBOARD BUILD")
    print("=" * 72)

    build()

    print()
    print("=" * 72)
    print("TRACKER PIPELINE COMPLETED SUCCESSFULLY")
    print("=" * 72)


if __name__ == "__main__":
    run_pipeline()
