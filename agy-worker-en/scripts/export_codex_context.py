"""Export the current (or parent) Codex session transcript to a lean Markdown file.

Applies homologous compaction (reusing Codex's latest compacted checkpoint when
plaintext is available) and strips tool calls, tool outputs, reasoning traces,
and inline base64 images so AGY receives clean dialogue context with zero extra
Codex output tokens.
"""

import argparse
import json
import os
from pathlib import Path
import re
import sys

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DEFAULT_MAX_MESSAGES = 30
DEFAULT_MAX_CHARS = 60000

BOILERPLATE_BLOCK_RE = re.compile(
    r"<(?:environment_context|INSTRUCTIONS|permissions instructions|collaboration_mode|AGENTS\.md)[^>]*>.*?"
    r"</(?:environment_context|INSTRUCTIONS|permissions instructions|collaboration_mode|AGENTS\.md)>",
    re.DOTALL | re.IGNORECASE,
)

SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{20,}"), r"\1[REDACTED_TOKEN]"),
)


def get_codex_home():
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def find_rollout_file(codex_home, thread_id):
    cid = (thread_id or "").strip()
    if not UUID_RE.match(cid):
        raise ValueError(f"invalid thread_id: {thread_id!r}")
    sessions_dir = Path(codex_home) / "sessions"
    if not sessions_dir.exists():
        raise FileNotFoundError(f"Codex sessions directory not found: {sessions_dir}")
    matches = sorted(
        sessions_dir.rglob(f"rollout-*-{cid}.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise FileNotFoundError(f"no rollout file found for thread_id={cid} under {sessions_dir}")
    return matches[0]


def extract_parent_thread_id(rollout_path):
    """Inspect session_meta at the start of a rollout for a parent thread UUID."""
    try:
        with open(rollout_path, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(10):
                line = fh.readline()
                if not line:
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "session_meta":
                    continue
                payload = rec.get("payload") or {}
                candidates = []
                source = payload.get("source")
                if isinstance(source, dict):
                    sub = source.get("subagent")
                    if isinstance(sub, dict):
                        ts = sub.get("thread_spawn")
                        if isinstance(ts, dict):
                            candidates.append(ts.get("parent_thread_id"))
                        candidates.append(sub.get("parent_thread_id"))
                candidates.append(payload.get("parent_thread_id"))
                candidates.append(payload.get("forked_from_id"))
                for cand in candidates:
                    if isinstance(cand, str) and UUID_RE.match(cand.strip()):
                        return cand.strip()
    except OSError:
        return None
    return None


def redact_secrets(text):
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def clean_message_text(text):
    cleaned = BOILERPLATE_BLOCK_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return redact_secrets(cleaned)


def extract_text_from_response_item(item):
    """Return (role, text) for a user/assistant message ResponseItem, else None."""
    if not isinstance(item, dict):
        return None
    if item.get("type") != "message":
        return None
    role = item.get("role")
    if role not in ("user", "assistant"):
        return None

    content = item.get("content")
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype in ("input_text", "output_text", "text"):
                txt = block.get("text")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt)
            elif btype in ("input_image", "image", "image_url"):
                parts.append("[image omitted]")
    text = clean_message_text("\n".join(parts))
    if not text:
        return None
    return role, text


def parse_rollout(rollout_path):
    """Parse a Codex rollout JSONL into (compaction_summary, messages, compacted_opaque)."""
    compaction_summary = None
    compacted_opaque = False
    messages = []

    with open(rollout_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue

            rtype = rec.get("type")
            payload = rec.get("payload")

            if rtype == "compacted" and isinstance(payload, dict):
                summary_msg = payload.get("message")
                repl_history = payload.get("replacement_history")
                extracted_repl = []
                if isinstance(repl_history, list):
                    for item in repl_history:
                        parsed = extract_text_from_response_item(item)
                        if parsed:
                            extracted_repl.append(parsed)

                has_plain_summary = isinstance(summary_msg, str) and bool(summary_msg.strip())
                if has_plain_summary or extracted_repl:
                    compaction_summary = clean_message_text(summary_msg) if has_plain_summary else None
                    messages = extracted_repl
                    compacted_opaque = False
                elif payload.get("encrypted_content"):
                    # Server-side encrypted compaction only: keep parsed plaintext turns as fallback
                    compacted_opaque = True
                continue

            if rtype == "response_item" and isinstance(payload, dict):
                parsed = extract_text_from_response_item(payload)
                if parsed:
                    messages.append(parsed)
                continue

            if rtype == "event_msg" and isinstance(payload, dict):
                etype = payload.get("type")
                if etype == "user_message" and isinstance(payload.get("message"), str):
                    text = clean_message_text(payload["message"])
                    if text and (not messages or messages[-1] != ("user", text)):
                        messages.append(("user", text))
                elif etype == "agent_message" and isinstance(payload.get("message"), str):
                    text = clean_message_text(payload["message"])
                    if text and (not messages or messages[-1] != ("assistant", text)):
                        messages.append(("assistant", text))

    return compaction_summary, messages, compacted_opaque


def render_markdown(thread_id, compaction_summary, messages, compacted_opaque, max_messages, max_chars):
    truncated_turns = 0
    if max_messages > 0 and len(messages) > max_messages:
        truncated_turns = len(messages) - max_messages
        messages = messages[-max_messages:]

    header_lines = [
        "# Codex Conversation Context (Tool Outputs Stripped)",
        f"- Source thread: `{thread_id}`",
    ]
    if compaction_summary:
        header_lines.append("- Includes homologous Codex compaction summary.")
    elif compacted_opaque:
        header_lines.append("- Note: earlier turns were server-side compacted; showing available plaintext turns.")
    if truncated_turns > 0:
        header_lines.append(f"- Omitted {truncated_turns} older message(s) to stay concise.")
    header = "\n".join(header_lines) + "\n"

    summary_block = ""
    if compaction_summary:
        summary_block = f"\n## Compaction Summary\n\n{compaction_summary}\n"

    rendered_entries = []
    for role, text in messages:
        label = "User" if role == "user" else "Assistant"
        rendered_entries.append(f"### {label}\n\n{text}\n")

    budget = max_chars - len(header) - len(summary_block) - 64
    kept = []
    used = 0
    dropped_for_budget = 0
    for entry in reversed(rendered_entries):
        if budget > 0 and used + len(entry) > budget and kept:
            dropped_for_budget += 1
            continue
        kept.append(entry)
        used += len(entry)
    kept.reverse()

    body = "\n".join(kept)
    if dropped_for_budget > 0:
        body = f"_[Omitted {dropped_for_budget} earlier message(s) to fit character budget]_\n\n" + body
    if not body and not summary_block:
        body = "_[No dialogue messages found in session]_\n"

    return f"{header}{summary_block}\n## Recent Dialogue\n\n{body}"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thread-id",
        help="Codex thread UUID to export (default: $CODEX_THREAD_ID; follows parent_thread_id if run inside a subagent)",
    )
    parser.add_argument(
        "--no-follow-parent",
        action="store_true",
        help="Do not follow session_meta parent_thread_id when using $CODEX_THREAD_ID",
    )
    parser.add_argument(
        "--out",
        help="Output Markdown file path (default: /tmp/agy-codex-context-<short-id>-<pid>.md)",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help=f"Maximum recent user/assistant messages to keep (default: {DEFAULT_MAX_MESSAGES})",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Maximum output character budget (default: {DEFAULT_MAX_CHARS})",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    explicit_tid = bool(args.thread_id)
    thread_id = (args.thread_id or os.environ.get("CODEX_THREAD_ID") or "").strip()
    if not thread_id:
        print(
            "export_codex_context: missing thread ID (pass --thread-id or ensure $CODEX_THREAD_ID is set)",
            file=sys.stderr,
        )
        return 1

    try:
        codex_home = get_codex_home()
        rollout_path = find_rollout_file(codex_home, thread_id)
        if not explicit_tid and not args.no_follow_parent:
            parent_id = extract_parent_thread_id(rollout_path)
            if parent_id and parent_id != thread_id:
                try:
                    rollout_path = find_rollout_file(codex_home, parent_id)
                    thread_id = parent_id
                except FileNotFoundError:
                    pass
        compaction_summary, messages, compacted_opaque = parse_rollout(rollout_path)
    except (OSError, ValueError) as exc:
        print(f"export_codex_context: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(
        thread_id,
        compaction_summary,
        messages,
        compacted_opaque,
        args.max_messages,
        args.max_chars,
    )

    out_path = (
        Path(args.out).expanduser()
        if args.out
        else Path("/tmp") / f"agy-codex-context-{thread_id[:8]}-{os.getpid()}.md"
    )
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(markdown)
    except OSError as exc:
        print(f"export_codex_context: failed to write {out_path}: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(f"{out_path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
