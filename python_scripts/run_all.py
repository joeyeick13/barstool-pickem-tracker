import os

from x_ingest import ingest
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

        # Verify unmatched totals directly
        # against the official Barstool
        # card image before ESPN matching.
        enrich_total_matchups()

    else:
        print(
            "Skipping X ingestion / "
            "total matchup verification: "
            "secrets not configured"
        )

    # Match picks to ESPN and save
    # kickoff/event ID before grading.
    enrich_schedule()

    # Grade completed games using
    # the stored ESPN match.
    grade_open()

    # Rebuild dashboard/site data.
    build()
