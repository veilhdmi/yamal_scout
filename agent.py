"""The scouting agent: Claude + tools, manual loop so the full history (incl. thinking blocks) is preserved.

`chat_stream()` yields UI events while the agent works (text deltas, tool calls, tool results);
`chat()` is the plain blocking version used by the CLI.
"""

import json
from collections.abc import Callable, Iterator
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
- The chat UI already draws a chart of every `predict_stat` distribution, so don't repeat the full table of
  probabilities in text: quote the few numbers that matter.
- Be concise and concrete; short paragraphs and bullet points read best in the chat.
  Stick to his public, professional life; do not speculate about his private life.
"""

TOOLS = [*TOOL_DEFINITIONS, WEB_SEARCH_TOOL]
MAX_TURNS = 12
# Tool results the UI can render (charts, fixture cards). Everything else only shows as a step.
UI_RESULT_TOOLS = {"predict_stat", "get_next_matches"}

client = anthropic.Anthropic()


def chat_stream(messages: list) -> Iterator[dict]:
    """Run the agent loop on `messages` (mutated in place with the full transcript), yielding UI events:

    {"type": "thinking"}                       the model started reasoning
    {"type": "text", "text": ...}              a chunk of the answer
    {"type": "tool_start", "id", "name", "input"}
    {"type": "tool_end", "id", "name", "data"?} data only for UI_RESULT_TOOLS
    {"type": "done"} / {"type": "error", "message": ...}
    """
    for _ in range(MAX_TURNS):
        # Client tool inputs here are a few dozen bytes, so eager input streaming would buy nothing
        # and would turn off the server-side `strict` validation that predict_stat's enums rely on.
        with client.beta.messages.stream(
            model=ANTHROPIC_MODEL,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=messages,
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "thinking":
                        yield {"type": "thinking"}
                    elif block.type == "server_tool_use":  # web search runs on Anthropic's side
                        yield {"type": "tool_start", "id": block.id, "name": block.name, "input": {}}
                    elif block.type == "web_search_tool_result":
                        yield {"type": "tool_end", "id": block.tool_use_id, "name": "web_search"}
                elif event.type == "text":
                    yield {"type": "text", "text": event.text}
            response = stream.get_final_message()

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            yield {"type": "error", "message": "The model declined this request. Try rephrasing it."}
            return
        if response.stop_reason == "pause_turn":  # server-side web search paused; resend to continue
            continue
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            yield {"type": "done"}
            return

        results = []
        for block in tool_uses:
            yield {"type": "tool_start", "id": block.id, "name": block.name, "input": block.input}
            output = run_tool(block.name, block.input)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
            end = {"type": "tool_end", "id": block.id, "name": block.name}
            if block.name in UI_RESULT_TOOLS:
                end["data"] = json.loads(output)
            yield end
        messages.append({"role": "user", "content": results})

    yield {"type": "error", "message": "Stopped: too many tool steps for one question."}


def chat(messages: list, on_tool: Callable[[str, dict], None] | None = None) -> str:
    """Blocking version of `chat_stream`. Returns the final text."""
    parts: list[str] = []
    for event in chat_stream(messages):
        if event["type"] == "text":
            parts.append(event["text"])
        elif event["type"] == "tool_start":
            parts.clear()  # keep only the final answer, not text written before a tool call
            if on_tool:
                on_tool(event["name"], event["input"])
        elif event["type"] == "error":
            return event["message"]
    return "".join(parts)


if __name__ == "__main__":
    import sys

    history: list = []
    question = " ".join(sys.argv[1:]) or "¿Cuántos tiros al arco crees que haga Lamine en su próximo partido?"
    history.append({"role": "user", "content": question})
    print(chat(history, on_tool=lambda n, a: print(f"  [tool] {n} {a}")))
