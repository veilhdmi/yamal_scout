"""Client-side tools the agent can call. Each returns a JSON string."""

import json
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import requests
from google.cloud import bigquery

import model
from config import BQ_LOCATION, ESPN_CLUB_TEAM_ID, ESPN_NATIONAL_TEAM_ID, FCT_VIEW, GCP_PROJECT, HTTP_HEADERS

MAX_BYTES_BILLED = 100 * 1024 * 1024  # 100 MB hard cap per query: the agent can never run up a bill
MAX_ROWS = 60
COMPETITION_BY_SLUG = {
    "esp.1": "league",
    "uefa.champions": "champions_league",
    "esp.copa_del_rey": "domestic_cup",
    "esp.super_cup": "domestic_cup",
}

TOOL_DEFINITIONS = [
    {
        "name": "query_stats",
        "description": (
            "Run a read-only BigQuery SQL SELECT over Lamine Yamal's match-by-match stats. "
            f"The only table is `{FCT_VIEW}` (one row per match he played). Columns: event_id, game_date (TIMESTAMP), "
            "match_date (DATE), season (INT, start year of the season), team ('Barcelona'|'Spain'), opponent, home_away "
            "('home'|'away'), is_home (BOOL), score (his team's goals first), result ('W'|'D'|'L'), league_slug, "
            "competition, competition_type ('league'|'champions_league'|'domestic_cup'|'national_team'|'friendly'), "
            "stage, goals, assists, shots, shots_on_target, fouls_suffered, fouls_committed, offsides, yellow_cards, "
            "red_cards, ingested_at, career_match_number (1 = debut, friendlies counted), is_friendly (BOOL). "
            "There is no minutes-played column. Friendlies have unreliable shots_on_target (always 0). "
            f"Returns at most {MAX_ROWS} rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."}},
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "predict_stat",
        "description": (
            "Statistical forecast (Poisson / negative binomial regression on his match history) for ONE stat in his "
            "next match. Returns the expected value, the full probability distribution, P(at least k), an 80% interval, "
            "the model coefficients and a walk-forward backtest over his last 50 matches (log-likelihood vs a career-average "
            "baseline, Brier score, interval coverage). This is the ONLY valid source for probabilities you report."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": model.TARGETS},
                "home": {"type": "boolean", "description": "True if his team plays at home."},
                "competition_type": {"type": "string", "enum": model.COMPETITIONS},
            },
            "required": ["target", "home", "competition_type"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_next_matches",
        "description": "Upcoming fixtures for Barcelona and Spain (ESPN), soonest first, with home/away and competition_type.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_recent_news",
        "description": (
            "Recent headlines about Lamine Yamal from Google News (title, source, date, link). Use it to check injuries, "
            "suspensions, rotation or anything the statistical model cannot see."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look-back window in days (1-30)."},
                "extra_terms": {"type": "string", "description": "Optional extra keywords, e.g. 'lesión' or 'Villarreal'."},
            },
            "required": ["days"],
            "additionalProperties": False,
        },
    },
]

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


def query_stats(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    if ";" in stripped or not re.match(r"^\s*(select|with)\b", stripped, re.IGNORECASE):
        return json.dumps({"error": "Only a single SELECT / WITH statement is allowed."})
    client = bigquery.Client(project=GCP_PROJECT, location=BQ_LOCATION)
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED)
    try:
        df = client.query(stripped, job_config=cfg).to_dataframe(create_bqstorage_client=False)
    except Exception as exc:  # surface SQL errors to the model so it can fix its query
        return json.dumps({"error": str(exc)[:800]})
    return json.dumps(
        {"row_count": len(df), "rows": json.loads(df.head(MAX_ROWS).to_json(orient="records", date_format="iso"))},
        ensure_ascii=False,
    )


def predict_stat(target: str, home: bool, competition_type: str) -> str:
    try:
        return json.dumps(model.predict(target, home, competition_type), ensure_ascii=False)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


def _fixtures(team_id: str) -> list[dict]:
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule?fixture=true"
    events = requests.get(url, headers=HTTP_HEADERS, timeout=20).json().get("events", [])
    out = []
    for e in events:
        comp = e["competitions"][0]
        side = next((c for c in comp["competitors"] if c["team"]["id"] == team_id), None)
        rival = next((c for c in comp["competitors"] if c["team"]["id"] != team_id), None)
        slug = (e.get("league") or {}).get("slug", "") if isinstance(e.get("league"), dict) else ""
        out.append({
            "date_utc": e["date"],
            "team": "Spain" if team_id == ESPN_NATIONAL_TEAM_ID else "Barcelona",
            "opponent": rival["team"]["displayName"] if rival else None,
            "home": side["homeAway"] == "home" if side else None,
            "competition": (e.get("league") or {}).get("name") if isinstance(e.get("league"), dict) else None,
            "competition_type": "national_team" if team_id == ESPN_NATIONAL_TEAM_ID else COMPETITION_BY_SLUG.get(slug, "league"),
        })
    return out


def get_next_matches() -> str:
    now = datetime.now(timezone.utc).isoformat()
    fixtures = [f for t in (ESPN_CLUB_TEAM_ID, ESPN_NATIONAL_TEAM_ID) for f in _fixtures(t) if f["date_utc"] >= now[:16]]
    fixtures.sort(key=lambda f: f["date_utc"])
    return json.dumps({"today_utc": now[:10], "upcoming": fixtures[:5]}, ensure_ascii=False)


def get_recent_news(days: int, extra_terms: str = "") -> str:
    days = min(max(int(days), 1), 30)
    q = quote_plus(f'"Lamine Yamal" {extra_terms} when:{days}d'.replace("  ", " "))
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=es-419&gl=US&ceid=US:es-419")
    items = [
        {"title": e.title, "source": getattr(e, "source", {}).get("title"), "published": e.get("published"), "link": e.link}
        for e in feed.entries[:15]
    ]
    return json.dumps({"count": len(items), "articles": items}, ensure_ascii=False)


def run_tool(name: str, args: dict) -> str:
    handlers = {
        "query_stats": lambda: query_stats(args["sql"]),
        "predict_stat": lambda: predict_stat(args["target"], args["home"], args["competition_type"]),
        "get_next_matches": get_next_matches,
        "get_recent_news": lambda: get_recent_news(args.get("days", 7), args.get("extra_terms", "")),
    }
    if name not in handlers:
        return json.dumps({"error": f"Unknown tool {name}"})
    try:
        return handlers[name]()
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"[:800]})
