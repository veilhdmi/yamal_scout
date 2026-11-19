"""Data behind the dashboard panels: season numbers, recent form, next fixture, model forecast, news.

Everything is cached for CACHE_TTL seconds; when it expires the model caches are cleared too, so a fresh
`ingest_espn.py` + `dbt build` shows up without restarting the server.
"""

import json
import threading
import time

import pandas as pd

import model
import tools

CACHE_TTL = 30 * 60
STAT_LABELS = {"shots_on_target": "Tiros al arco", "shots": "Tiros", "goals": "Goles", "assists": "Asistencias"}

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key: str, compute):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
    if hit and key in ("overview", "forecast"):  # expired: reload matches from BigQuery and refit
        model.load_matches.cache_clear()
        model.backtest.cache_clear()
    value = compute()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def refresh() -> None:
    with _lock:
        _cache.clear()
    model.load_matches.cache_clear()
    model.backtest.cache_clear()


def _season_label(season: int) -> str:
    return f"{season}-{str(season + 1)[-2:]}"


def overview() -> dict:
    def compute():
        df = model.load_matches()  # competitive matches only, oldest first
        club = df[df["team"] == "Barcelona"]
        season = int(club["season"].max())
        cur = club[club["season"] == season]

        def totals(frame: pd.DataFrame) -> dict:
            return {
                "matches": int(len(frame)),
                "goals": int(frame["goals"].sum()),
                "assists": int(frame["assists"].sum()),
                "shots": int(frame["shots"].sum()),
                "shots_on_target": int(frame["shots_on_target"].sum()),
            }

        recent = df.tail(10).iloc[::-1]
        return {
            "season": {"label": _season_label(season), "team": "FC Barcelona", **totals(cur)},
            "career": totals(df),
            "recent": [
                {
                    "date": r.match_date.isoformat(),
                    "team": r.team,
                    "opponent": r.opponent,
                    "opponent_id": r.opponent_id,
                    "home": bool(r.is_home),
                    "score": r.score,
                    "result": r.result,
                    "competition": r.competition,
                    "goals": int(r.goals),
                    "assists": int(r.assists),
                    "shots": int(r.shots),
                    "shots_on_target": int(r.shots_on_target),
                }
                for r in recent.itertuples()
            ],
        }

    return _cached("overview", compute)


def fixtures() -> dict:
    return _cached("fixtures", lambda: json.loads(tools.get_next_matches()))


def forecast() -> dict:
    def compute():
        upcoming = fixtures().get("upcoming", [])
        nxt = upcoming[0] if upcoming else None
        home = bool(nxt["home"]) if nxt else True
        comp = nxt["competition_type"] if nxt and nxt["competition_type"] in model.COMPETITIONS else "league"
        out = {}
        for target in model.TARGETS:
            p = model.predict(target, home, comp)
            bt = p["backtest_last_matches"]
            out[target] = {
                "label": STAT_LABELS[target],
                "expected_value": p["expected_value"],
                "most_likely_value": p["most_likely_value"],
                "p_most_likely": p["p_most_likely"],
                "p_exact": p["p_exact"],
                "p_at_least": p["p_at_least"],
                "interval_80pct": p["interval_80pct"],
                "backtest": {
                    "matches": bt["matches_evaluated"],
                    "beats_baseline": bt["beats_baseline"],
                    "mae": bt["mae"],
                    "coverage_80pct": bt["coverage_80pct_interval"],
                    "predicted_p_ge1": bt["predicted_avg_p_at_least_1"],
                    "observed_p_ge1": bt["observed_rate_at_least_1"],
                },
            }
        return {"match": nxt, "context": {"home": home, "competition_type": comp}, "stats": out}

    return _cached("forecast", compute)


def news() -> dict:
    return _cached("news", lambda: json.loads(tools.get_recent_news(7)))
