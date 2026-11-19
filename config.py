import os

from dotenv import load_dotenv

load_dotenv()

GCP_PROJECT = os.environ["GCP_PROJECT"]
BQ_DATASET = os.getenv("BQ_DATASET", "yamal_scout")  # raw layer, written by ingest_espn.py
BQ_MARTS_DATASET = os.getenv("BQ_MARTS_DATASET", "yamal_marts")  # built by dbt_yamal_scout
BQ_LOCATION = os.getenv("BQ_LOCATION", "US")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")

# ESPN identifiers
ESPN_ATHLETE_ID = "362150"  # Lamine Yamal
ESPN_CLUB_TEAM_ID = "83"  # FC Barcelona
ESPN_NATIONAL_TEAM_ID = "164"  # Spain

RAW_TABLE = f"{GCP_PROJECT}.{BQ_DATASET}.raw_espn_gamelog"
FCT_VIEW = f"{GCP_PROJECT}.{BQ_MARTS_DATASET}.fct_lamine_matches"

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (yamal-scout prototype)"}
