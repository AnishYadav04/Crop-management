"""Search for agricultural guidance when a maintenance event is recorded."""

from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import psycopg


TRUSTED_SUFFIXES = (
    "gov.in",
    "gov",
    "edu",
    "edu.in",
    "ac.in",
    "icar.org.in",
)
MAX_RESULTS = 6


def openai_web_research(query: str, details: dict) -> tuple[str, list[dict]] | None:
    """Optionally synthesize advice with cited OpenAI web search when configured."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    body = {
        "model": os.getenv("OPENAI_RESEARCH_MODEL", "gpt-6-astra"),
        "tools": [{"type": "web_search"}],
        "tool_choice": "required",
        "input": (
            "Research cautious crop-management steps for this weather event. "
            "Use reliable agricultural extension, government, or university sources where possible. "
            "Do not diagnose a disease from weather alone. Give practical checks and distinguish "
            "general guidance from crop-specific advice. Cite claims with the supplied web citations.\n"
            f"Event context: {json.dumps(details)}\nSearch query: {query}"
        ),
    }
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        result = json.load(response)
    text_parts, citations = [], []
    for item in result.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text_parts.append(content.get("text", ""))
                for annotation in content.get("annotations", []):
                    if annotation.get("type") == "url_citation":
                        citation = annotation.get("url_citation", annotation)
                        url = citation.get("url")
                        if url and not any(source["url"] == url for source in citations):
                            citations.append({"title": citation.get("title") or url, "url": url,
                                              "snippet": "Cited by the research response.",
                                              "trusted_domain": _is_trusted(url)})
    if not text_parts:
        raise ValueError("The research response contained no cited text")
    return "\n\n".join(text_parts), citations[:MAX_RESULTS]


def make_query(event_type: str, details: dict) -> str:
    crop = details.get("crop_name")
    stage = details.get("growth_stage")
    crop_context = " ".join(value for value in (crop, stage) if value and value != "unspecified")
    if event_type == "weather_rain_onset_after_temperature_drop":
        query = (
            "Haryana agricultural university crop management after rainfall "
            "cool weather leaf wetness drainage disease prevention"
        )
    else:
        query = f"agricultural crop management guidance {event_type.replace('_', ' ')}"
    return f"{crop_context} {query}".strip()


def _is_trusted(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in TRUSTED_SUFFIXES)


def search_sources(query: str) -> list[dict]:
    from ddgs import DDGS

    raw = DDGS().text(query, region="in-en", safesearch="moderate", max_results=12)
    results = []
    seen = set()
    for item in raw:
        url = item.get("href") or item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "title": item.get("title", "Untitled source"),
                "url": url,
                "snippet": item.get("body", ""),
                "trusted_domain": _is_trusted(url),
            }
        )
    results.sort(key=lambda item: not item["trusted_domain"])
    return results[:MAX_RESULTS]


def summarize_event(event_type: str, details: dict, sources: list[dict]) -> str:
    temp_drop = details.get("temperature_drop_c")
    rain = details.get("rainfall_interval_mm")
    trusted_count = sum(source["trusted_domain"] for source in sources)
    crop_name = details.get("crop_name", "unspecified")
    growth_stage = details.get("growth_stage", "unspecified")
    if details.get("demo"):
        note = (
            "This is a synthetic presentation alert, not a measured field event. "
            f"It demonstrates how the system would research a sugarcane weather concern for "
            f"{crop_name} at stage {growth_stage}. Use the linked sources as general guidance "
            "and confirm actual field conditions before acting."
        )
    else:
        source_kind = details.get("source_kind", "unknown source")
        interval_seconds = details.get("rainfall_interval_seconds")
        interval_text = f"the preceding {interval_seconds}-second interval" if interval_seconds else "an interval of unspecified duration"
        temperature_hours = details.get("temperature_window_hours")
        window_text = f"{temperature_hours} hours" if temperature_hours else "an unspecified comparison window"
        note = (
            f"A {source_kind} feed reported rain ({rain} mm over {interval_text}) "
            f"after a {temp_drop} °C temperature drop over {window_text}. "
            "This is a weather-condition alert, not a confirmed crop problem. "
            "Check the crop and field conditions, especially standing water and prolonged canopy wetness, "
            f"then use the linked guidance for {crop_name} at {growth_stage} if that context is known."
        )
    if trusted_count == 0:
        note += " Search returned no recognized government or university domains; verify sources manually."
    else:
        note += f" Search found {trusted_count} result(s) on recognized government or university domains."
    return note


def research_pending_events(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        # Recover tasks left in progress by a worker restart or network timeout.
        cur.execute(
            """UPDATE maintenance_events SET research_status='pending'
                 WHERE research_status='searching' AND created_at < now()-interval '10 minutes'"""
        )
        cur.execute(
            """
            SELECT id, event_type, details
            FROM maintenance_events
            WHERE research_status = 'pending'
               OR (research_status IN ('failed','no_results')
                   AND research_attempts < 3
                   AND COALESCE(research_next_attempt_at,created_at) <= now())
            ORDER BY detected_at
            LIMIT 5
            FOR UPDATE SKIP LOCKED
            """
        )
        pending = cur.fetchall()
        for event_id, _event_type, _details in pending:
            cur.execute(
                "UPDATE maintenance_events SET research_status = 'searching', research_attempts=research_attempts+1 WHERE id = %s",
                (event_id,),
            )
    conn.commit()

    for event_id, event_type, details in pending:
        try:
            if isinstance(details, str):
                details = json.loads(details)
            query = make_query(event_type, details)
            ai_result = openai_web_research(query, details)
            if ai_result:
                note, sources = ai_result
            else:
                sources = search_sources(query)
                note = summarize_event(event_type, details, sources)
            status = "completed" if sources else "no_results"
            error = None
        except Exception as exc:
            query = make_query(event_type, details if isinstance(details, dict) else {})
            sources = []
            note = "Research search did not complete. Review the event and search query manually."
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE maintenance_events
                SET research_status = %s, search_query = %s, research_note = %s,
                    sources = %s::jsonb, researched_at = %s, research_error = %s,
                    research_next_attempt_at = CASE
                        WHEN %s IN ('failed','no_results') AND research_attempts < 3
                        THEN now() + (interval '1 minute' * research_attempts)
                        ELSE NULL END
                WHERE id = %s
                """,
                (
                    status,
                    query,
                    note,
                    json.dumps(sources),
                    datetime.now(ZoneInfo("Asia/Kolkata")),
                    error,
                    status,
                    event_id,
                ),
            )
        conn.commit()
        if status == "completed":
            print(f"Research complete for event {event_id}: {note}", flush=True)
        else:
            print(f"Research {status} for event {event_id}: {error or 'no sources found'}", flush=True)
