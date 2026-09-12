import os

from x_ingest import ingest
from data_corrections import apply_verified_corrections
from total_matchup_enrich import enrich_total_matchups
from schedule_enrich import enrich_schedule
from grade import grade_open
from build_site import build


if __name__ == "__main__":

    if (
        os.getenv("X_BEARER_TOKEN")
        and os.getenv("OPENAI_API_KEY")
    ):
        ingest()

    else:
        print(
            "Skipping X ingestion: "
            "secrets not configured"
        )

    # Apply picks that have been manually verified
    # against the official Barstool Pick Em card.
    #
    # This runs every time so bad extraction data
    # cannot reintroduce a known correction.
    apply_verified_corrections()

    if (
        os.getenv("X_BEARER_TOKEN")
        and os.getenv("OPENAI_API_KEY")
    ):
        # Verify any remaining unmatched totals
        # against their official Barstool source.
        enrich_total_matchups()

    else:
        print(
            "Skipping total matchup verification: "
            "secrets not configured"
        )

    # Match picks to ESPN and refresh kickoff/event data.
    enrich_schedule()

    # Grade completed games using the stored ESPN event.
    grade_open()

    # Rebuild dashboard/site data.
    build()
