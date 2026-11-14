"""Poll PostgreSQL for new events and run the web research agent."""
from __future__ import annotations

import os
import time
import psycopg

from services.research_agent import research_pending_events


def main():
    database_url = os.environ["DATABASE_URL"]
    interval = max(10, int(os.getenv("RESEARCH_POLL_SECONDS", "30")))
    print(f"Research agent worker started; polling every {interval}s", flush=True)
    while True:
        try:
            with psycopg.connect(database_url) as conn:
                research_pending_events(conn)
        except Exception as exc:
            print(f"Research worker cycle failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
