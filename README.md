# AGY Worker (Codex Skill)

A Codex skill that delegates subtasks to Gemini via the local Google Antigravity CLI (`agy`).

Available in two editions:
- `agy-worker/` — Chinese edition
- `agy-worker-en/` — English edition

## Highlights

- **Dedicated execution modes**: Separates `read-only`, `plan` (`--mode plan`), `accept-edits`, and `auto` (edits + sandboxed checks).
- **Lean Codex subagent shell**: Prefers running inside an isolated native Codex subagent (avoiding full parent history forks and extra work) so progress stays visible in Codex's subagents panel, while clearly attributing results to external `AGY CLI` and its real `conversation_id`.
- **Zero-output-token context export (`scripts/export_codex_context.py`)**: When the main agent decides prior conversation history is needed, the subagent exports the current/parent Codex rollout into a temporary `/tmp/*.md`—reusing Codex's homologous compaction checkpoint and stripping tool outputs—and passes it via `run_agy.py --context-file ... --delete-context-file`.
- **Native `agy` controls**: Passes `--model` (default `gemini-4-neon`) and `--effort` (`low|medium|high|xhigh`), plus `--conversation`, `--sandbox`, `--add-dir`, `--json-schema`, and default `10m` `--print-timeout` with a subprocess fallback timeout.
- **Reliable wrapper (`scripts/run_agy.py`)**: Validates JSON envelopes, streams oversized prompts via `stdin` to avoid `ARG_MAX`, preserves `conversation_id`/`error` on failures, and supports explicit UUID-scoped **cleanup** (`delete <id>` / `--delete-after`).
- **Consent & safety guardrails**: Never passes `--dangerously-skip-permissions` by default, and respects existing AGY `deny > ask > allow` rules.

## Installation

Copy either edition into your Codex skills directory:
- User scope: `$HOME/.agents/skills/agy-worker`
- Repo scope: `.agents/skills/agy-worker`

Make sure `agy` is installed and authenticated via an interactive `agy` session first.

## Configuration

Edit the **User Preferences** block at the top of `SKILL.md` (and `agents/openai.yaml` when changing implicit invocation):

| Option | Values | Default | Purpose |
| --- | --- | --- | --- |
| `trigger_mode` | `auto-eligible` \| `explicit-only` | `auto-eligible` | Whether Codex may proactively delegate matching tasks or only trigger when the user explicitly requests AGY/Antigravity/Gemini. (For `explicit-only`, also set `allow_implicit_invocation: false` in `agents/openai.yaml`.) |
| `preferred_tasks` | YAML list | Second-opinion reviews, frontend UI/UX, human-readable docs/reports, detailed explanations of complex questions | Soft preference for tasks to consider delegating to AGY instead of a general Codex subagent. Keep frontmatter `description` in sync when editing. |
| `conversation_retention` | `always` \| `never` \| `auto` | `auto` | Controls whether Codex reuses AGY sessions via `--conversation` during a task and whether temporary AGY conversations are explicitly deleted (`delete` / `--delete-after`) when the subagent finishes. |


## Real-World A/B Benchmark

Tested on a 3-turn read-only research + delegated report workflow (`codex --no-daemon`, `gpt-6.2-sol`, `medium` reasoning effort) on a multi-document research workspace (strictly read-only; reports written to `/tmp`), comparing the pre-optimization skill vs. the optimized skill:

| Metric | Before (Pre-Optimization) | After (Optimized) | Delta |
| --- | ---: | ---: | ---: |
| Subagent fork mode | Default (full parent fork) | `fork_turns: "none"` (isolated) | Isolated shell |
| Subagent rollout size | 789.6 KB | 287.7 KB | **-63.6%** |
| Subagent `input_tokens` | 2,898,247 | 738,406 | **-74.5%** |
| Subagent `output_tokens` | 6,105 | 3,223 | **-47.2%** |
| Subagent `reasoning_output_tokens` | 883 | 31 | **-96.5%** |
| Combined Codex `total_tokens` (Main + Subagent) | 5,202,722 | 2,442,119 | **-53.1%** |
| Report generated in `/tmp` | Yes (40.7 KB, 319 lines) | Yes (32.1 KB, 155 lines) | Both succeeded |
