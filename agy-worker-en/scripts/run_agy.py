"""Run one local Antigravity task and emit its validated JSON envelope."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import subprocess

DEFAULT_MODEL = "gemini-4-neon"
DEFAULT_EFFORT = "medium"
DEFAULT_PRINT_TIMEOUT = "10m"
SUBPROCESS_GRACE_SECONDS = 30.0
MAX_INLINE_PROMPT_BYTES = 32768

RUN_MODES = ("read-only", "plan", "accept-edits", "auto")
ALL_MODES = (*RUN_MODES, "delete")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MODEL_EFFORT_SUFFIX_RE = re.compile(r"-(?:low|medium|high|xhigh|max)$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|μs|ms|s|m|h)")
DURATION_UNITS_SECONDS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "μs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}


def parse_duration_seconds(value):
    """Parse a non-negative Go-style duration string into seconds."""
    text = value.strip()
    if text == "0":
        return 0.0
    pos = 0
    total = 0.0
    for match in DURATION_PART_RE.finditer(text):
        if match.start() != pos:
            raise ValueError(f"invalid duration {value!r}")
        amount = float(match.group(1))
        unit = match.group(2)
        total += amount * DURATION_UNITS_SECONDS[unit]
        pos = match.end()
    if pos == 0 or pos != len(text):
        raise ValueError(f"invalid duration {value!r}")
    return total


def normalize_effort(effort):
    if effort is None:
        return None
    return "xhigh" if effort == "extra high" else effort


def get_agy_cli_dir():
    override = os.environ.get("AGY_CLI_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemini" / "antigravity-cli"


def delete_conversation(conversation_id, cli_dir=None):
    """Best-effort cleanup of one local AGY CLI conversation by strict UUID."""
    cid = (conversation_id or "").strip()
    if not UUID_RE.match(cid):
        raise ValueError(f"invalid conversation_id for deletion: {conversation_id!r}")

    base = Path(cli_dir) if cli_dir is not None else get_agy_cli_dir()
    removed = []
    warnings = []

    file_targets = [
        base / "conversations" / f"{cid}.db",
        base / "conversations" / f"{cid}.db-wal",
        base / "conversations" / f"{cid}.db-shm",
        base / "conversations" / f"{cid}.pb",
        base / "annotations" / f"{cid}.pbtxt",
    ]
    for path in file_targets:
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            warnings.append(f"failed to remove {path}: {exc}")

    brain_dir = base / "brain" / cid
    if brain_dir.exists():
        try:
            shutil.rmtree(brain_dir)
            removed.append(str(brain_dir))
        except OSError as exc:
            warnings.append(f"failed to remove {brain_dir}: {exc}")

    summary_db = base / "conversation_summaries.db"
    if summary_db.exists():
        try:
            conn = sqlite3.connect(str(summary_db), timeout=5.0)
            try:
                cur = conn.cursor()
                tables = {
                    row[0]
                    for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                deleted_rows = 0
                for table in ("conversation_summaries", "conversations_fts", "conversation_parents"):
                    if table in tables:
                        cur.execute(f"DELETE FROM {table} WHERE conversation_id = ?", (cid,))
                        if cur.rowcount and cur.rowcount > 0:
                            deleted_rows += cur.rowcount
                conn.commit()
                if deleted_rows > 0:
                    removed.append(f"{summary_db} ({deleted_rows} rows)")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            warnings.append(f"failed to update {summary_db}: {exc}")

    return removed, warnings


def validate(stdout, returncode, require_structured=False):
    problems = []
    conversation_id = None
    result = None
    if returncode != 0:
        problems.append(f"agy exited with code {returncode}")

    stripped = stdout.strip()
    if not stripped:
        if returncode == 0:
            problems.append("empty stdout from agy")
        raise ValueError("; ".join(problems))

    try:
        result = json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError as exc:
        if returncode == 0:
            problems.append(f"invalid JSON on stdout ({exc})")
        else:
            snippet = stripped.decode("utf-8", errors="replace")[:200]
            problems.append(f"non-JSON stdout ({snippet!r})")
    else:
        if not isinstance(result, dict):
            problems.append("expected one JSON object on stdout")
        else:
            cid = result.get("conversation_id")
            if isinstance(cid, str) and cid.strip():
                conversation_id = cid.strip()
            if result.get("status") != "SUCCESS":
                problems.append(f"status={result.get('status')!r}")
            if result.get("error") not in (None, ""):
                problems.append(f"error={result['error']!r}")
            if require_structured:
                if result.get("structured_output") is None:
                    problems.append("missing structured_output for --json-schema")
            else:
                response = result.get("response")
                if not isinstance(response, str) or not response.strip():
                    problems.append("missing or empty text response")

    if problems:
        if conversation_id:
            problems.insert(0, f"conversation_id={conversation_id}")
        raise ValueError("; ".join(problems))

    return result


def build_instruction(mode):
    if mode == "read-only":
        return "Use read-only operations. Do not modify workspace files. Answer directly in your response."
    if mode == "plan":
        return (
            "Use read-only operations. Do not modify workspace files. "
            "Produce a structured plan and rationale in your response."
        )
    if mode == "auto":
        return (
            "Apply the requested edits within scope and run relevant verification checks in the sandbox. "
            "Report actual changes and check results."
        )
    return "Apply the requested edits within scope. Report actual changes and checks."


def build_command(args, instruction, task):
    combined = f"{instruction}\n\n{task}"
    use_stdin = len(combined.encode("utf-8")) > MAX_INLINE_PROMPT_BYTES
    prompt_arg = instruction if use_stdin else combined

    command = [
        "agy",
        "-p",
        prompt_arg,
        "--model",
        args.model,
        "--output-format",
        "json",
    ]
    if args.mode != "read-only":
        command.extend(["--mode", args.mode])
    if args.resolved_effort:
        command.extend(["--effort", args.resolved_effort])
    if args.print_timeout_seconds > 0:
        command.extend(["--print-timeout", args.print_timeout])
    if args.conversation:
        command.extend(["--conversation", args.conversation])
    if args.sandbox:
        command.append("--sandbox")
    for directory in args.add_dir:
        command.extend(["--add-dir", directory])
    if args.json_schema:
        command.extend(["--json-schema", args.json_schema])

    stdin_bytes = task.encode("utf-8") if use_stdin else None
    return command, stdin_bytes


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=ALL_MODES)
    parser.add_argument(
        "target",
        nargs="?",
        help="Conversation ID when mode is 'delete' (alternative to --conversation)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"AGY model slug (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--effort",
        choices=[*EFFORT_LEVELS, "extra high"],
        default=None,
        help=f"Reasoning effort level (default: {DEFAULT_EFFORT}, omitted if --model includes an effort suffix)",
    )
    parser.add_argument("--prompt", help="Task text (default: read stdin)")
    parser.add_argument(
        "--print-timeout",
        default=DEFAULT_PRINT_TIMEOUT,
        help=f"AGY print timeout duration, e.g. 10m; pass 0s to disable (default: {DEFAULT_PRINT_TIMEOUT})",
    )
    parser.add_argument("--conversation", help="Resume an existing AGY conversation by ID, or target ID for 'delete'")
    parser.add_argument(
        "--delete-after",
        action="store_true",
        help="Best-effort delete the local AGY conversation after a successful run",
    )
    parser.add_argument("--sandbox", action="store_true", help="Enable AGY terminal sandbox restrictions")
    parser.add_argument("--add-dir", action="append", default=[], help="Add an extra workspace directory (repeatable)")
    parser.add_argument("--json-schema", help="JSON schema string or file path for structured_output")
    args = parser.parse_args(argv)

    if args.mode == "delete":
        target_id = args.conversation or args.target
        if not target_id:
            parser.error("mode 'delete' requires a conversation ID (positional or --conversation)")
        if args.conversation and args.target and args.conversation != args.target:
            parser.error("conflicting conversation IDs provided to 'delete'")
        args.delete_target = target_id
        return args, parser

    if args.target is not None:
        parser.error(f"unexpected positional argument {args.target!r} for mode {args.mode!r}")

    try:
        args.print_timeout_seconds = parse_duration_seconds(args.print_timeout)
    except ValueError as exc:
        parser.error(str(exc))

    has_model_suffix = bool(MODEL_EFFORT_SUFFIX_RE.search(args.model))
    normalized_effort = normalize_effort(args.effort)
    if has_model_suffix and normalized_effort is not None:
        parser.error("do not combine an effort-suffixed --model with --effort")
    args.resolved_effort = None if has_model_suffix else (normalized_effort or DEFAULT_EFFORT)
    return args, parser


def main(argv=None):
    args, parser = parse_args(argv)

    if args.mode == "delete":
        try:
            removed, warnings = delete_conversation(args.delete_target)
        except ValueError as exc:
            print(f"agy-worker: {exc}", file=sys.stderr)
            return 1
        for warning in warnings:
            print(f"agy-worker: warning: {warning}", file=sys.stderr)
        payload = {
            "conversation_id": args.delete_target.strip(),
            "deleted": len(removed) > 0,
            "removed": removed,
            "warnings": warnings,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0 if not warnings else 1

    task = args.prompt if args.prompt is not None else sys.stdin.read()
    if not task.strip():
        parser.error("a non-empty task is required")

    instruction = build_instruction(args.mode)
    command, stdin_bytes = build_command(args, instruction, task)
    subprocess_timeout = (
        args.print_timeout_seconds + SUBPROCESS_GRACE_SECONDS
        if args.print_timeout_seconds > 0
        else None
    )

    try:
        if stdin_bytes is not None:
            run = subprocess.run(
                command,
                input=stdin_bytes,
                stdout=subprocess.PIPE,
                timeout=subprocess_timeout,
            )
        else:
            run = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                timeout=subprocess_timeout,
            )
        result = validate(run.stdout, run.returncode, require_structured=bool(args.json_schema))
    except subprocess.TimeoutExpired:
        print(
            f"agy-worker: timed out after {args.print_timeout} (+{int(SUBPROCESS_GRACE_SECONDS)}s grace)",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        print(f"agy-worker: {exc}", file=sys.stderr)
        return 1

    if args.delete_after:
        cid = result.get("conversation_id") if isinstance(result, dict) else None
        if isinstance(cid, str) and cid.strip():
            try:
                _, warnings = delete_conversation(cid)
                for warning in warnings:
                    print(f"agy-worker: cleanup warning: {warning}", file=sys.stderr)
            except ValueError as exc:
                print(f"agy-worker: cleanup warning: {exc}", file=sys.stderr)

    sys.stdout.buffer.write(run.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
