# AGY Worker (Codex Skill)

A Codex skill that delegates subtasks to Gemini via the local Google Antigravity CLI (`agy`).

Available in two editions:
- `agy-worker/` — Chinese edition
- `agy-worker-en/` — English edition

## Highlights

- **Dedicated execution modes**: Separates `read-only`, `plan` (`--mode plan`), `accept-edits`, and `auto` (edits + sandboxed checks).
- **Codex subagent-first**: Prefers running inside a native Codex subagent so progress stays visible in Codex's subagents panel, while clearly attributing results to external `AGY CLI` and its real `conversation_id`.
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
