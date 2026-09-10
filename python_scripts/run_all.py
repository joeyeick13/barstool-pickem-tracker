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

    # Match picks to ESPN and save kickoff/event ID
    # before any grading happens.
    enrich_schedule()

    # Grade completed games using the stored ESPN match.
    grade_open()

    # Rebuild dashboard/site data.
    build()
