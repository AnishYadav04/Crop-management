CREATE TABLE IF NOT EXISTS maintenance_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    location_name TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    details JSONB NOT NULL,
    research_status TEXT NOT NULL DEFAULT 'pending',
    search_query TEXT,
    research_note TEXT,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    researched_at TIMESTAMPTZ,
    research_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS maintenance_events_location_time_idx
    ON maintenance_events (location_name, detected_at DESC);

CREATE INDEX IF NOT EXISTS maintenance_events_research_status_idx
    ON maintenance_events (research_status, created_at);
