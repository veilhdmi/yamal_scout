"""The scouting agent: Claude + tools, manual loop so the full history (incl. thinking blocks) is preserved."""

from collections.abc import Callable
from datetime import date

import anthropic

from config import ANTHROPIC_MODEL
from tools import TOOL_DEFINITIONS, WEB_SEARCH_TOOL, run_tool

SYSTEM_PROMPT = f"""You are Yamal Scout, an analyst that follows Lamine Yamal (FC Barcelona, Spain) full time:
his match stats, upcoming fixtures, news, injuries and form. Today is {date.today().isoformat()}.

How you work:
- Answer in the language the user writes in.
- Facts about his matches come from `query_stats` (BigQuery). Never invent numbers.
- For any forecast ("how many shots on target next match?", "will he score?"):
  1. Call `get_next_matches` to identify the next fixture (home/away, competition).
  2. Call `predict_stat` for the relevant stat. Its probabilities are the ONLY probabilities you may quote.
  3. Call `get_recent_news` to check injuries, suspensions, rotation or anything the model cannot see.
  4. Answer with: the most likely value, P(at least k) for the relevant thresholds, the 80% interval,
     the main drivers (form, home, competition), and the backtest evidence (does it beat the baseline? is it calibrated?).
  5. Give a "confidence" verdict about the forecast itself (high/medium/low) and justify it with the backtest,
     the sample size and the news. If news suggests he may not play or will be limited, say so and explain how that
     changes the picture qualitatively - do not silently adjust the model's numbers.
- Use `web_search` only for things the other tools cannot answer (social media buzz, transfer rumours, quotes).
- Be concise and concrete. Stick to his public, professional life; do not speculate about his private life.
"""

TOOLS = [*TOOL_DEFINITIONS, WEB_SEARCH_TOOL]
MAX_TURNS = 12

client = anthropic.Anthropic()


def chat(messages: list, on_tool: Callable[[str, dict], None] | None = None) -> str:
    """Run the agent loop on `messages` (mutated in place with the full transcript). Returns the final text."""
    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            return "The model declined this request. Try rephrasing it."
        if response.stop_reason == "pause_turn":  # server-side web search paused; resend to continue
            continue
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        results = []
        for block in response.content:
            if block.type == "tool_use":
                if on_tool:
                    on_tool(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": run_tool(block.name, block.input)})
        messages.append({"role": "user", "content": results})
    return "Stopped: too many tool steps for one question."


if __name__ == "__main__":
    import sys

    history: list = []
    question = " ".join(sys.argv[1:]) or "¿Cuántos tiros al arco crees que haga Lamine en su próximo partido?"
    history.append({"role": "user", "content": question})
    print(chat(history, on_tool=lambda n, a: print(f"  [tool] {n} {a}")))
