# Yamal Scout

A chat agent that follows Lamine Yamal full time — match stats, fixtures, news — and answers questions like *"how many shots on target will he have next match?"* with a statistical forecast, real probabilities and an honest confidence verdict.

## Why it's built this way

An LLM asked "how many shots will he take?" will happily make up a number. Here the LLM never produces the probabilities: a count model trained on his match history does, and a walk-forward backtest says how much to trust it. Claude's job is orchestration and explanation — find the next fixture, run the model, check the news for things the model can't see (injuries, rotation), and put it together.

## How it works

```
ESPN public API ──► ingest_espn.py ──► BigQuery raw_espn_gamelog ──► dbt_yamal_scout ──► fct_lamine_matches
                                                                                            │
Google News RSS ─┐                                                                          ▼
ESPN fixtures ───┼──► tools.py ◄── model.py (Poisson / NegBin + backtest)
Claude web search┘        │
                          ▼
                 agent.py (Claude, streaming tool loop) ──► server.py (FastAPI + SSE) ──► web/ (dashboard + chat)
```

1. **Ingest** — `ingest_espn.py` pulls every match he's played (Barcelona + Spain, all competitions, 2023→today) from ESPN's game log: goals, assists, shots, shots on target, fouls, cards. ESPN returns the full history on each call, so the load is a full refresh (`WRITE_TRUNCATE`) — idempotent, safe to run after every match.
2. **Transform** — the dbt project [`dbt_yamal_scout`](https://github.com/veilhdmi/dbt_yamal_scout) cleans the raw table (staging) and builds the marts in `yamal_marts`, with 28 data tests (uniqueness, accepted values, a feature-leakage check).
3. **Model** — `model.py` reads `yamal_marts.fct_lamine_matches` and fits a Poisson regression per stat on pre-match features only (recent form, career average, home/away, competition, experience) and switches to a negative binomial when the data is overdispersed.
4. **Backtest** — for each of his last 50 matches the model is trained only on earlier matches and scored against a career-average baseline (log-likelihood, Brier score for P(≥1), 80% interval coverage).
5. **Agent** — `agent.py` runs Claude with four tools (`query_stats`, `predict_stat`, `get_next_matches`, `get_recent_news`) plus Anthropic's web search, streaming its answer and each tool step as events.
6. **Web app** — `server.py` (FastAPI) serves a single-page dashboard in `web/` (plain HTML/CSS/JS, no build step): season numbers, next-match countdown, the model's probability distribution for each stat, recent form, a news ticker, and a chat that streams the answer, shows every tool step live and draws a chart for each forecast the agent runs.

### Current backtest (last 50 matches)

| Stat | Beats baseline | MAE | Predicted vs observed P(≥1) | 80% interval coverage |
|---|---|---|---|---|
| Shots on target | ✅ | 1.02 | 0.76 vs 0.72 | 94% |
| Shots | ✅ | 1.77 | 0.98 vs 0.94 | 90% |
| Goals | ✅ | 0.64 | 0.31 vs 0.50 (under-predicts his current streak) | 88% |
| Assists | ✅ (barely) | 0.55 | 0.30 vs 0.32 | 90% |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
gcloud auth application-default login
```

Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY` and `GCP_PROJECT`. Then:

```bash
python ingest_espn.py          # load / refresh the raw table
# then, in dbt_yamal_scout:  dbt build
python model.py                # print forecasts + backtests
python agent.py "¿Cuántos tiros al arco hará en su próximo partido?"
python server.py               # web app on http://localhost:8000
python server.py --lan         # also reachable from phones on the same Wi-Fi
```

## Cost

- **BigQuery**: within the free tier (~200 rows). Agent queries are capped at 100 MB billed each.
- **Data sources**: ESPN and Google News RSS are free, no key.
- **Claude**: a few cents per question with Claude Opus 5.5 (4–6 tool calls per forecast question).

## Known limitations

- No minutes played: a 10-minute cameo counts the same as 90 minutes.
- No opponent-strength feature yet.
- ESPN records 0 shots on target in friendlies, so friendlies are excluded from the model. Spain coverage before 2024 is partial.
- Social media is covered only indirectly (news + web search); there's no official API access to his accounts.

## Next steps

- Schedule ingestion (GitHub Actions cron or Cloud Scheduler) after every matchday.
- Add minutes played and opponent defensive stats (shots conceded per match) as features.
- Log every forecast and score it after the match — a live calibration track record.
