---
name: agy-worker
description: Delegate a subtask to Gemini via the local Google Antigravity CLI (agy) for read-only analysis, code review, planning, implementation, or fixes. Use when the user explicitly asks for Antigravity/Gemini, or when policy permits delegating independent second-opinion reviews and design comparisons, frontend UI/UX design, human-readable docs or reports, or detailed explanations of complex questions.
---

# AGY Worker

## User Preferences (Editable)

```yaml
# 1. Trigger mode:
#    - auto-eligible (default): Not limited to explicit AGY/Antigravity/Gemini requests;
#      proactively consider delegating when a task matches preferred_tasks below.
#    - explicit-only: Trigger only when the user explicitly requests AGY / Antigravity / Gemini.
#      (If set to explicit-only, also set allow_implicit_invocation: false in agents/openai.yaml.)
trigger_mode: auto-eligible

# 2. Task preferences compared to a general Codex subagent (soft preference, not a hard rule):
#    Prefer delegating these scenarios to AGY (Gemini 4 Neon); keep tasks that rely heavily
#    on Codex's native toolchain directly in Codex.
#    Update the skill frontmatter description if you edit this list.
preferred_tasks:
  - Independent "second-opinion" reviews and design comparisons when complexity warrants it (PR/code reviews, edge-case audits, cross-model blind-spot checks)
  - Frontend UI/UX design (visual direction, complex layouts, CSS/interactive prototypes)
  - Generating human-readable documents, reports, and write-ups [Strongly Recommended]
  - Detailed explanations when the user asks complex questions

# 3. AGY conversation retention and cleanup policy (controls reuse and explicit deletion of temporary AGY sessions):
#    - 1 (always): Always keep AGY conversations without auto-deleting; reuse via --conversation when continuing the same task.
#    - 2 (never): Never keep; start a fresh session each time and explicitly delete temporary AGY conversations when the subagent finishes (or pass --delete-after for single-turn runs).
#    - 3 (auto, default): Let Codex decide reuse and end-of-task retention/deletion using the criteria below.
conversation_retention: auto
```

**Criteria for `conversation_retention: auto`:**
- **Reuse during a task (`--conversation <id>`):**
  - **Reuse**: Continuation turns on the same goal—follow-up questions refining AGY's previous response, implementing edits directly from a fresh review/plan, resuming after a timeout or permission block, or continuing work across the same files AGY just read.
  - **Start fresh**: Switching to an unrelated subtask, requesting an unbiased independent second review, stale context after major workspace edits, or a long session that drifted or hit repeated errors.
- **Keep vs. explicitly delete when the subagent/task finishes:**
  - **Explicitly delete** (run `run_agy.py delete <conversation_id>`, or pass `--delete-after` for one-shot tasks): One-off read-only lookups, standalone reviews, temporary doc/explanation generation, and completed temporary subagent sessions unlikely to receive follow-up turns.
  - **Keep**: Intermediate results of a multi-stage workflow, open tasks likely to need follow-up or rework, interrupted/failed runs kept for debugging or resumption, or sessions the user explicitly wants to inspect or continue in `agy`.

---

## Subagent Execution & Panel Visibility

- **Prefer running this skill inside a native Codex subagent when policy allows**, and explicitly ensure the task **appears in Codex's subagents panel**. [Codex Subagents Docs](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- **Keep the subagent shell minimal (no extra work)**: When spawning the subagent, **avoid full parent history forks** (e.g., use `fork_turns: "none"`) and pass only a concise task brief. The subagent should strictly act as a thin runner—optionally export context (if requested by the main agent) → run `scripts/run_agy.py` → clean up temporary files/sessions per policy → return the result—without doing extra codebase exploration or rework on its own.
- **On-demand homologous context export**: Let the **main agent** decide (just as when invoking native subagents) whether prior conversation history is needed. When it is, instruct the subagent to run `scripts/export_codex_context.py` before launching `agy` (reusing Codex's homologous compaction summary and stripping tool-output noise into `/tmp/*.md` with zero extra Codex output tokens), then pass it via `run_agy.py --context-file <path> --delete-context-file`.
- If policy or the current environment disallows spawning subagents, run the script directly from the main agent.
- **Attribution boundaries**: Whether invoked from the main agent or a Codex subagent, always label the output as coming from external `AGY CLI` and cite AGY's actual `conversation_id` when tracking, resuming, or cleaning up. Never fabricate a Codex native `agent_role` or `agent_path`, and never pass an AGY `conversation_id` to Codex native subagent tools.

---

## Running the Script

Run `scripts/run_agy.py` from the **target workspace** using the path of the currently loaded skill directory (Codex discovers skills in `.agents/skills/agy-worker` at repo scope and `$HOME/.agents/skills/agy-worker` at user scope, with legacy support for `$CODEX_HOME/skills/agy-worker`; always resolve relative to the loaded `SKILL.md`). Give the worker a clear task description, allowed read/write paths, expected output, acceptance criteria, necessary context/already-ruled-out options, and any required artifacts.

| Delegated Task | Mode | Worker Instructions |
| --- | --- | --- |
| Reading, analysis, review, Q&A, docs/explanations | `read-only` | Restrict strictly to read-only operations and answer directly with evidence (does not pass `--mode plan`; `--sandbox` recommended). |
| Architecture design, implementation planning | `plan` | Explore read-only and return a structured plan with rationale (passes `--mode plan`; `--sandbox` recommended). |
| Feature implementation, bug fixes (file edits only) | `accept-edits` | Edit directly within authorized scope; report actual changes, checks, and blocked steps. |
| Implementation plus safe test/build checks in sandbox | `auto` | Edit within scope and run relevant verification in AGY's sandbox (requires local AGY support with terminal sandbox enabled). |

**Model & Effort**: Defaults to `--model gemini-4-neon --effort medium`, passing native `agy` `--model` and `--effort` flags (`low|medium|high|xhigh`; `extra high` maps to `xhigh`). Override `--model` with another slug from local `agy models` if needed; do not combine an effort-suffixed model ID with `--effort`. If a model is unavailable, check local `agy models` and report it.

```bash
# Read-only analysis/review (without /plan)
python3 "<skill-dir>/scripts/run_agy.py" read-only --sandbox --delete-after <<'TASK'
Read-only inspect src/cache.py. Identify where cache keys are generated and list the relevant function names.
TASK

# When prior conversation context is needed: export homologous compacted context first, then auto-delete temp file
ctx=$(python3 "<skill-dir>/scripts/export_codex_context.py")
python3 "<skill-dir>/scripts/run_agy.py" read-only --sandbox --context-file "$ctx" --delete-context-file --delete-after <<'TASK'
Using the exported conversation context above, review edge cases in src/cache.py.
TASK

# Explicitly delete a temporary AGY conversation when a subagent finishes (UUID-scoped local agy-cli cleanup)
python3 "<skill-dir>/scripts/run_agy.py" delete "<conversation_id>"
```

**Common Options**:
- `--prompt <text>`: Pass the task inline (otherwise reads stdin; oversized tasks automatically move the task body to stdin to avoid `ARG_MAX` limits).
- `--context-file <path>` / `--delete-context-file`: Prepend the temporary Markdown exported by `export_codex_context.py` and automatically remove it after the run.
- `--conversation <id>`: Resume an existing AGY conversation per the retention policy (or specify the target ID for `delete`).
- `--delete-after`: Automatically clean up the newly created local AGY conversation after a successful single-turn run.
- `--print-timeout <duration>`: Defaults to `10m` (pass `0s` to disable); the script also enforces a subprocess fallback timeout to prevent headless hangs.
- `--sandbox`: Enable AGY terminal sandbox restrictions.
- `--add-dir <path>`: Attach an additional workspace directory (repeatable).
- `--json-schema <schema-or-path>`: Require AGY to emit schema-validated `structured_output`.

---

## Sandbox & Permissions

- **External data consent & rejection handling**: Before sending this project's code, architecture, or internal context to the external AGY service for the **first time** in a session—unless the user has already explicitly authorized sharing it—briefly ask the user to confirm that sending the relevant code/context (and approximate scope) to local `agy` (Google Antigravity) is allowed before running the script. If Codex's `auto-reviewer` still rejects the call, do not repeatedly retry the same command; explain the rejection reason and intended scope to the user and wait for explicit authorization or a Codex `.rules` allowlist entry.
- **Codex sandbox**: Running `run_agy.py` requires outbound network access to model APIs and read access to local AGY credentials (e.g., `~/.gemini/antigravity-cli/`); `delete` and `--delete-after` also require write access to `~/.gemini/antigravity-cli/` to clean up local session files and index entries; running `export_codex_context.py` when needed requires read access to the single current/parent rollout file under `${CODEX_HOME:-$HOME/.codex}/sessions/`. If Codex's sandbox blocks execution and causes `Please sign in`, network, or cleanup/read errors, request approval/elevation rather than assuming the user is logged out of AGY. First-time AGY authentication must be completed in an interactive `agy` session.
- **AGY permissions**: Neither `read-only` (omits `--mode`, using default mode + read-only prompt) nor `plan` (passes `--mode plan`) is a hard permission lock—pair them with `--sandbox` (for session-scoped overrides, see `agy --settings`, without overriding existing `deny` rules). `accept-edits` auto-approves workspace file edits while terminal commands remain subject to `deny > ask > allow`; `auto` auto-approves edits and safe sandboxed commands. Never pass `--dangerously-skip-permissions` by default. When extra permissions are needed, report the blocked action and minimal `action(target)` rule while preserving the user's existing settings.

---

## Verification & Tracking

- The script emits raw JSON only when `agy` exits with code 0, stdout is a single JSON object, `status == "SUCCESS"`, `error` is empty, and `response` is non-empty (or `structured_output` is non-null when `--json-schema` is used).
- Check both the exit code and stderr. On failure, if AGY returned JSON, stderr includes `conversation_id`, `status`, and `error` for tracking or resumption.
- Headless mode may skip tools requiring interactive approval and still exit normally; if required actions were denied or skipped, report the actual completed scope. For write tasks, inspect actual diffs (e.g., `git diff`) and run relevant tests, and check for partial edits before retrying after a failure.
- Because `agy` CLI does not yet provide an official non-interactive delete subcommand, `delete` / `--delete-after` strictly validates the UUID and performs best-effort cleanup of that session's local files and `conversation_summaries.db` rows under `~/.gemini/antigravity-cli/`.

[CLI Reference](https://antigravity.google/docs/cli/reference) · [Headless Mode](https://antigravity.google/docs/cli/headless) · [Permissions](https://antigravity.google/docs/cli/permissions) · [Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
