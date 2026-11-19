"""Pull Lamine Yamal's full match log from ESPN's public API and load it into BigQuery.

ESPN returns the complete history on every call, so the load is a full refresh
(WRITE_TRUNCATE): idempotent and safe to run on a schedule after every match.
"""

from datetime import datetime, timezone

import pandas as pd
import requests
from google.cloud import bigquery

from config import (
    BQ_DATASET,
    BQ_LOCATION,
    ESPN_ATHLETE_ID,
    GCP_PROJECT,
    HTTP_HEADERS,
    RAW_TABLE,
)

GAMELOG_URL = (
    f"https://site.web.api.espn.com/apis/common/v3/sports/soccer/athletes/{ESPN_ATHLETE_ID}/gamelog"
)
STAT_COLUMNS = {
    "totalGoals": "goals",
    "goalAssists": "assists",
    "totalShots": "shots",
    "shotsOnTarget": "shots_on_target",
    "foulsCommitted": "fouls_committed",
    "foulsSuffered": "fouls_suffered",
    "offsides": "offsides",
    "yellowCards": "yellow_cards",
    "redCards": "red_cards",
}


def fetch(params: dict | None = None) -> dict:
    resp = requests.get(GAMELOG_URL, params=params, headers=HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def filter_options(payload: dict, name: str) -> list[dict]:
    for f in payload.get("filters", []):
        if f["name"] == name:
            return f.get("options", [])
    return []


def parse_rows(payload: dict, season: str, team_id: str, league_slug: str) -> list[dict]:
    meta = payload.get("events", {})
    names = payload.get("names", [])
    rows = []
    for season_type in payload.get("seasonTypes", []):
        for category in season_type.get("categories", []):
            for ev in category.get("events", []):
                m = meta.get(ev["eventId"], {})
                stats = dict(zip(names, ev.get("stats", [])))
                row = {
                    "event_id": ev["eventId"],
                    "game_date": m.get("gameDate"),
                    "season": int(season),
                    "team_id": team_id,
                    "team_name": (m.get("team") or {}).get("abbreviation"),
                    "opponent_id": (m.get("opponent") or {}).get("id"),
                    "opponent_name": (m.get("opponent") or {}).get("displayName"),
                    "home_away": "home" if m.get("atVs") == "vs" else "away",
                    "score": m.get("score"),
                    "result": m.get("gameResult"),
                    "league_slug": league_slug,
                    "league_name": m.get("leagueName"),
                    "stage": season_type.get("displayName"),
                }
                for src, dst in STAT_COLUMNS.items():
                    val = stats.get(src)
                    row[dst] = int(float(val)) if val not in (None, "", "-", "--") else None
                rows.append(row)
    return rows


def collect() -> pd.DataFrame:
    base = fetch()
    seasons = [o["value"] for o in filter_options(base, "season")]
    rows: list[dict] = []
    for season in seasons:
        season_payload = fetch({"season": season})
        for team in filter_options(season_payload, "team"):
            team_payload = fetch({"season": season, "team": team["value"]})
            for league in filter_options(team_payload, "league"):
                payload = fetch({"season": season, "team": team["value"], "league": league["value"]})
                got = parse_rows(payload, season, team["value"], league["value"])
                print(f"  {season} | {team['displayValue']:<9} | {league['displayValue']:<35} {len(got):>3} matches")
                rows.extend(got)
    df = pd.DataFrame(rows).drop_duplicates(subset="event_id")
    df["game_date"] = pd.to_datetime(df["game_date"], utc=True)
    df["ingested_at"] = datetime.now(timezone.utc)
    return df.sort_values("game_date").reset_index(drop=True)


def load(df: pd.DataFrame) -> None:
    client = bigquery.Client(project=GCP_PROJECT, location=BQ_LOCATION)
    client.create_dataset(bigquery.Dataset(f"{GCP_PROJECT}.{BQ_DATASET}"), exists_ok=True)
    job = client.load_table_from_dataframe(
        df, RAW_TABLE, job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    )
    job.result()
    print(f"Loaded {len(df)} rows into {RAW_TABLE}")
    print("Next: run `dbt build` in dbt_yamal_scout to rebuild the marts.")


if __name__ == "__main__":
    print("Fetching ESPN game log...")
    data = collect()
    print(f"{len(data)} unique matches, {data['game_date'].min():%Y-%m-%d} -> {data['game_date'].max():%Y-%m-%d}")
    load(data)
