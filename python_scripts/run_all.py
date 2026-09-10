import os

from x_ingest import ingest
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


    # Match every provisional pick to its ESPN game
    # BEFORE grading so kickoff times and event IDs
    # are available before the game begins.
    enrich_schedule()


    # Grade completed games using the stored event ID.
    grade_open()


    # Rebuild dashboard.
    build()
