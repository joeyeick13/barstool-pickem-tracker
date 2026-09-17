import os

from x_ingest import ingest
from data_corrections import apply_verified_corrections
from total_matchup_enrich import enrich_total_matchups
from schedule_enrich import enrich_schedule
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
    #
    # TEMPORARY LEGACY LAYER.
    #
    # This exists only to preserve corrections that were
    # manually verified before the permanent ingestion and
    # identity architecture was installed.
    #
    # IMPORTANT:
    # Do NOT add Week 4, Week 5, or future weekly card patches
    # here. New weeks must work through the permanent pipeline.
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 2 — HISTORICAL VERIFIED CORRECTIONS")
    print("=" * 72)

    apply_verified_corrections()

    # ========================================================
    # STEP 3 — LEGACY TOTAL MATCHUP ENRICHMENT
    # ========================================================
    #
    # This remains temporarily for older stored wagers that
    # were created before transactional structured ingestion.
    #
    # New wagers should already carry their game identity from
    # x_ingest.py.
    #
    # This stage must NOT become the normal way future weekly
    # cards are repaired.
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
    #
    # schedule_enrich.py uses the shared exhaustive ESPN
    # resolver and locks each provisional CFB wager to one
    # deterministic ESPN event_id.
    #
    # Once an event_id is stored, it is treated as the game's
    # identity. Missing stored event IDs are not silently
    # rematched to another game.
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 4 — ESPN EVENT LOCKING")
    print("=" * 72)

    enrich_schedule()

    # ========================================================
    # STEP 5 — GRADING
    # ========================================================
    #
    # grade.py grades completed games from the stored ESPN
    # event identity using the same shared resolver
    # architecture.
    #
    # Official PAT HILL reconciled rows remain authoritative.
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 5 — GRADING")
    print("=" * 72)

    grade_open()

    # ========================================================
    # STEP 6 — PERMANENT INTEGRITY AUDIT
    # ========================================================
    #
    # CRITICAL:
    #
    # The dashboard is NOT rebuilt until this audit passes.
    #
    # If ingestion, identity resolution, event locking, or
    # grading produces structurally unsafe data, run_audit()
    # raises an exception and GitHub Actions fails.
    #
    # That prevents silently publishing corrupted tracker data.
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 6 — TRACKER INTEGRITY AUDIT")
    print("=" * 72)

    run_audit()

    # ========================================================
    # STEP 7 — DASHBOARD BUILD
    # ========================================================
    #
    # Reached only after the tracker passes the permanent
    # integrity audit.
    # ========================================================

    print()
    print("=" * 72)
    print("STEP 7 — DASHBOARD BUILD")
    print("=" * 72)

    build()

    print()
    print("=" * 72)
    print("TRACKER PIPELINE COMPLETED SUCCESSFULLY")
    print("=" * 72)


if __name__ == "__main__":
    run_pipeline()
