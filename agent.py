"""
Interactive command-line agent for the KSA Labor Law assistant.

Loads the system prompt and tool schemas, then runs a REPL that drives the
Claude tool-use loop: each user message is sent to Claude, any tool calls
Claude makes are dispatched locally and the results fed back, looping
until Claude returns a final text answer.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from tools import run_tool


MODEL = "claude-opus-4-7"
MAX_TOKENS = 4096
HERE = Path(__file__).parent


def load_system_prompt() -> str:
    return (HERE / "system_prompt.md").read_text(encoding="utf-8")


def load_tool_schemas() -> list[dict]:
    data = json.loads((HERE / "tool_schemas.json").read_text(encoding="utf-8"))
    return data["tools"]


def run_turn(client: Anthropic, system: str, tools: list[dict], messages: list[dict]) -> str:
    """Run one user turn through Claude, handling the tool-use loop."""
    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            # Final answer: concatenate any text blocks.
            return "".join(
                block.text for block in response.content if block.type == "text"
            )

        # Execute every tool_use block in the assistant turn and reply with
        # a single user message containing the corresponding tool_result blocks.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"  [tool] {block.name}({json.dumps(block.input)})")
            result = run_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})


def main() -> int:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to a .env file or export it.",
              file=sys.stderr)
        return 1

    client = Anthropic()
    system = load_system_prompt()
    tools = load_tool_schemas()
    messages: list[dict] = []

    print("KSA Labor Law agent. Type your question, or 'exit' to quit.\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            return 0

        messages.append({"role": "user", "content": user_input})
        try:
            answer = run_turn(client, system, tools, messages)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            # Drop the failed user message so the conversation stays consistent.
            messages.pop()
            continue

        print(f"\n{answer}\n")


if __name__ == "__main__":
    sys.exit(main())
