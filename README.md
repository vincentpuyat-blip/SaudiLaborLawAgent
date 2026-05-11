# KSA Labor Law Agent (Keepers Internal)

An internal compliance and calculation assistant for Keepers' finance and accounting consultants working under Saudi labor law. The agent runs Claude with a strict system prompt and a set of deterministic calculation tools so that every numeric answer is computed in code (not by the model) and every legal claim is traceable to a specific article.

## Repository layout

| File | Purpose |
| --- | --- |
| `system_prompt.md` | The agent's instructions: jurisdiction, sources, tool-use discipline, output structure. |
| `tool_schemas.json` | JSONSchema definitions for every tool exposed to Claude. |
| `eos_calculator.py` | Deterministic implementation of Articles 84-88 (End-of-Service Award). Self-contained with 15 worked-example tests. |
| `tools.py` | Dispatches tool calls from Claude to Python functions. Currently only `calculate_end_of_service` is implemented; the others are stubs. |
| `agent.py` | Interactive CLI that loads the prompt and schemas and runs the tool-use loop against `claude-opus-4-7`. |
| `requirements.txt` | Python dependencies. |

## Setup

1. Python 3.10+ is recommended.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Provide your API key. Create a `.env` file in the repository root:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

   `agent.py` calls `load_dotenv()` on startup, so the key is picked up automatically. You can alternatively `export ANTHROPIC_API_KEY=...` in your shell.

## Verifying the calculator

Run the EOS test suite before relying on any output:

```bash
python3 eos_calculator.py
```

You should see 15 passing tests.

## Running the agent

```bash
python3 agent.py
```

You'll get a `>` prompt. Type a question and the agent will respond. Tool calls Claude makes are echoed to the terminal as `[tool] <name>(<args>)` so you can see what was computed. Type `exit` (or Ctrl-D) to quit.

### Example question

```
> An employee on an unlimited-term contract resigned after 7 years and 3 months
  of service. Her last monthly wage (basic + housing + transport) is SAR 18,500.
  Start date 2018-02-15, end date 2025-05-20. What is her statutory EOS?
```

The agent should call `calculate_end_of_service` and return a structured answer with inputs used, the SAR figure, the calculation basis (5 years at half-month + 2.x years at full-month, two-thirds modifier under Article 85), legal citations (Articles 84 and 85), and any caveats.

## Implemented tools

| Tool | Status |
| --- | --- |
| `calculate_end_of_service` | Implemented (Articles 84-88). |
| `calculate_gosi_contributions` | Stub. |
| `calculate_notice_period` | Stub. |
| `calculate_annual_leave` | Stub. |
| `calculate_overtime` | Stub. |
| `check_nitaqat_compliance` | Stub. |
| `retrieve_legal_provisions` | Stub. |

Stubs return `{"error": "Not yet implemented"}`. The agent is instructed not to perform calculations itself, so for unimplemented areas it should report the gap rather than guess.

## Notes

- This tool is for use by Keepers consultants. It is not a substitute for licensed Saudi legal counsel.
- The Arabic text of the law is authoritative where Arabic and English versions differ. The agent is instructed to flag material divergences.
