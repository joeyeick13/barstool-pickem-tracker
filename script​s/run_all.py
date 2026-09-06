import os
from x_ingest import ingest
from grade import grade_open
from build_site import build

if __name__ == "__main__":
    if os.getenv("X_BEARER_TOKEN") and os.getenv("OPENAI_API_KEY"):
        ingest()
    else:
        print("Skipping X ingestion: secrets not configured")
    grade_open()
    build()
