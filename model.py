"""Count model for Lamine Yamal's per-match stats (shots on target, shots, goals, assists).

Poisson regression on pre-match features (no leakage: every feature uses only
matches before the one being predicted). If the training residuals show
overdispersion, predictions use a negative binomial with the same mean.
A walk-forward backtest measures whether the probabilities are trustworthy.
"""

from functools import lru_cache

import numpy as np
import pandas as pd
from google.cloud import bigquery
from scipy import stats
from sklearn.linear_model import PoissonRegressor

from config import BQ_LOCATION, FCT_VIEW, GCP_PROJECT

TARGETS = ["shots_on_target", "shots", "goals", "assists"]
COMPETITIONS = ["league", "champions_league", "domestic_cup", "national_team"]
BACKTEST_MATCHES = 50
MIN_TRAIN = 40


@lru_cache(maxsize=1)
def load_matches() -> pd.DataFrame:
    client = bigquery.Client(project=GCP_PROJECT, location=BQ_LOCATION)
    df = client.query(f"select * from `{FCT_VIEW}` order by game_date").to_dataframe(create_bqstorage_client=False)
    # ESPN does not record shots on target in friendlies (0 SOG out of every shot) -> drop them.
    return df[df["competition_type"] != "friendly"].reset_index(drop=True)


def feature_frame(df: pd.DataFrame, target: str) -> pd.DataFrame:
    y = df[target].astype(float)
    prior = y.shift(1)  # only information available before kick-off
    X = pd.DataFrame(index=df.index)
    X["form_ewm10"] = prior.ewm(span=10, min_periods=1).mean()
    X["career_mean"] = prior.expanding().mean()
    X["form_last5"] = prior.rolling(5, min_periods=1).mean()
    X["home"] = (df["home_away"] == "home").astype(float)
    for comp in COMPETITIONS[1:]:  # 'league' is the baseline category
        X[f"comp_{comp}"] = (df["competition_type"] == comp).astype(float)
    X["experience"] = np.log1p(np.arange(len(df)))  # he was 15 at debut; his output grows with age
    return X


def next_match_features(df: pd.DataFrame, target: str, home: bool, competition_type: str) -> pd.DataFrame:
    y = df[target].astype(float)
    row = {
        "form_ewm10": y.ewm(span=10).mean().iloc[-1],
        "career_mean": y.mean(),
        "form_last5": y.tail(5).mean(),
        "home": float(home),
        **{f"comp_{c}": float(competition_type == c) for c in COMPETITIONS[1:]},
        "experience": np.log1p(len(df)),
    }
    return pd.DataFrame([row])


def fit(X: pd.DataFrame, y: pd.Series) -> tuple[PoissonRegressor, float]:
    model = PoissonRegressor(alpha=0.5, max_iter=1000).fit(X, y)
    mu = model.predict(X)
    dispersion = float(np.sum((y - mu) ** 2 / mu) / max(len(y) - X.shape[1] - 1, 1))
    return model, max(dispersion, 1.0)


def distribution(mu: float, dispersion: float, max_k: int = 8) -> np.ndarray:
    k = np.arange(max_k + 1)
    if dispersion > 1.1:  # negative binomial with var = dispersion * mu
        n = mu / (dispersion - 1)
        pmf = stats.nbinom.pmf(k, n, n / (n + mu))
    else:
        pmf = stats.poisson.pmf(k, mu)
    return pmf


def summarize(pmf: np.ndarray) -> dict:
    cdf = np.cumsum(pmf)
    most_likely = int(np.argmax(pmf))
    lo = int(np.searchsorted(cdf, 0.10))
    hi = int(np.searchsorted(cdf, 0.90))
    return {
        "p_exact": {str(k): round(float(p), 3) for k, p in enumerate(pmf[:6])},
        "p_at_least": {f">={k}": round(float(1 - cdf[k - 1]), 3) for k in range(1, 6)},
        "most_likely_value": most_likely,
        "p_most_likely": round(float(pmf[most_likely]), 3),
        "interval_80pct": [lo, hi],
    }


@lru_cache(maxsize=8)
def backtest(target: str) -> dict:
    """Walk-forward: for each of the last N matches, train on everything before it, then predict."""
    df = load_matches()
    X, y = feature_frame(df, target), df[target].astype(float)
    start = max(MIN_TRAIN, len(df) - BACKTEST_MATCHES)
    rows = []
    for i in range(start, len(df)):
        model, disp = fit(X.iloc[1:i], y.iloc[1:i])  # row 0 has no prior history
        mu = float(model.predict(X.iloc[[i]])[0])
        pmf = distribution(mu, disp)
        actual = int(y.iloc[i])
        base_mu = max(float(y.iloc[:i].mean()), 1e-6)  # naive baseline: career average
        rows.append({
            "mu": mu,
            "actual": actual,
            "logp_model": np.log(max(pmf[min(actual, len(pmf) - 1)], 1e-9)),
            "logp_baseline": stats.poisson.logpmf(actual, base_mu),
            "p_ge1": 1 - pmf[0],
            "in_80": summarize(pmf)["interval_80pct"][0] <= actual <= summarize(pmf)["interval_80pct"][1],
        })
    bt = pd.DataFrame(rows)
    hit_ge1 = (bt["actual"] >= 1).astype(float)
    return {
        "matches_evaluated": len(bt),
        "mae": round(float((bt["mu"] - bt["actual"]).abs().mean()), 3),
        "avg_log_likelihood_model": round(float(bt["logp_model"].mean()), 3),
        "avg_log_likelihood_baseline_career_avg": round(float(bt["logp_baseline"].mean()), 3),
        "beats_baseline": bool(bt["logp_model"].mean() > bt["logp_baseline"].mean()),
        "brier_p_at_least_1": round(float(((bt["p_ge1"] - hit_ge1) ** 2).mean()), 3),
        "predicted_avg_p_at_least_1": round(float(bt["p_ge1"].mean()), 3),
        "observed_rate_at_least_1": round(float(hit_ge1.mean()), 3),
        "coverage_80pct_interval": round(float(bt["in_80"].mean()), 3),
    }


def predict(target: str, home: bool, competition_type: str) -> dict:
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}")
    if competition_type not in COMPETITIONS:
        raise ValueError(f"competition_type must be one of {COMPETITIONS}")
    df = load_matches()
    X, y = feature_frame(df, target), df[target].astype(float)
    model, disp = fit(X.iloc[1:], y.iloc[1:])
    x_next = next_match_features(df, target, home, competition_type)
    mu = float(model.predict(x_next)[0])
    return {
        "target": target,
        "context": {"home": home, "competition_type": competition_type},
        "expected_value": round(mu, 2),
        "distribution": "negative_binomial" if disp > 1.1 else "poisson",
        "dispersion": round(disp, 2),
        **summarize(distribution(mu, disp)),
        "features_used": {k: round(float(v), 3) for k, v in x_next.iloc[0].items()},
        "coefficients": {k: round(float(c), 3) for k, c in zip(X.columns, model.coef_)},
        "training_matches": int(len(df) - 1),
        "backtest_last_matches": backtest(target),
        "known_limitations": [
            "No minutes-played data: substitute appearances count as full matches.",
            "No opponent-strength feature yet (opponent defensive quality is not modeled).",
            "Injuries, rotation and lineup news are NOT in the model; check news before trusting it.",
        ],
    }


if __name__ == "__main__":
    import json

    for t in TARGETS:
        print(json.dumps(predict(t, home=True, competition_type="league"), indent=2, ensure_ascii=False))
