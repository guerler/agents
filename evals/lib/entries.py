"""What a run appended, as pi-durable records it, read into what grading needs.

The session hands back its conversation's entries; the chat shape grading reads, the tools a
run called, the guards that refused and the artifacts results carried are all read off them here.
"""

import json

REASONING_KEYS = ("reasoning_content", "reasoning")


def messages(entries):
    """The model messages entries contribute; the prompt is the agent's, not the conversation's."""
    return [m for e in entries or [] if e.get("kind") != "pi.system" for m in (e.get("model") or [])]


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if c.get("type") == "text")


def to_chat(entries):
    """The messages in the OpenAI chat shape grading reads."""
    out = []
    for m in messages(entries):
        role = m.get("role")
        if role == "assistant":
            message = {"role": "assistant", "content": _text(m.get("content")) or None}
            calls = [c for c in m.get("content") or [] if c.get("type") == "toolCall"]
            if calls:
                message["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments") or {})},
                    }
                    for c in calls
                ]
            thinking = [c for c in m.get("content") or [] if c.get("type") == "thinking" and c.get("thinking")]
            if thinking:
                key = thinking[0].get("thinkingSignature")
                message[key if key in REASONING_KEYS else REASONING_KEYS[0]] = "\n".join(
                    c["thinking"] for c in thinking
                )
            out.append(message)
        elif role == "toolResult":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("toolCallId"),
                    "name": m.get("toolName"),
                    "content": _text(m.get("content")),
                }
            )
        elif role == "user":
            out.append({"role": "user", "content": _text(m.get("content"))})
    return out


def _brief(value, limit=300):
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[:limit] + "…"


def digest(entries):
    """What a run did: the tools it called, the calls refused, its steps, artifacts, logs, error."""
    tools, refused, artifacts, logs = [], [], [], []
    steps, error = 0, None
    for m in messages(entries):
        if m.get("role") == "assistant":
            steps += 1
            if m.get("stopReason") == "error":
                error = m.get("errorMessage") or "the model request failed"
            for c in m.get("content") or []:
                if c.get("type") == "toolCall":
                    tools.append(c["name"])
                    logs.append(f"call {c['name']}({_brief(c.get('arguments') or {})})")
        elif m.get("role") == "toolResult":
            details = m.get("details") or {}
            artifacts.extend(details.get("artifacts") or [])
            if details.get("guard"):
                refused.append(m.get("toolName"))
                logs.append(f"  -> refused by the {details['guard']} guard")
            else:
                logs.append(f"  -> {_brief(_text(m.get('content')))}")
    return {
        "chat": to_chat(entries),
        "tools": tools,
        "refused": refused,
        "artifacts": artifacts,
        "logs": logs,
        "steps": steps,
        "error": error,
    }
