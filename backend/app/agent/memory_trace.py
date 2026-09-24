import ast
import copy
import json
from typing import Any


MEMORY_TOOL_NAMES = {
    "remember_memory",
    "list_memories",
    "update_memory",
    "forget_memory",
    "reset_memories",
}


def is_memory_tool(tool_name: str) -> bool:
    return tool_name in MEMORY_TOOL_NAMES


def sanitize_memory_args(tool_name: str, args: Any) -> dict:
    if not is_memory_tool(tool_name) or not isinstance(args, dict):
        return args if isinstance(args, dict) else {}
    sanitized = {}
    for field in ("memory_type", "workflow_scope", "memory_id"):
        if args.get(field) is not None:
            sanitized[field] = args[field]
    if "contact_scope" in args:
        sanitized["has_contact_scope"] = bool(args.get("contact_scope"))
    if "key" in args:
        sanitized["key_present"] = bool(args.get("key"))
    if "value" in args:
        sanitized["value_chars"] = len(str(args.get("value") or ""))
    return sanitized


def sanitize_memory_result(tool_name: str, output: Any) -> dict:
    payload = _coerce_mapping(output)
    if tool_name in {"remember_memory", "update_memory"}:
        memory = payload.get("memory") or {}
        return _memory_metadata(memory)
    if tool_name == "list_memories":
        memories = payload.get("memories") or []
        return {
            "result_count": int(payload.get("count", len(memories))),
            "memory_ids": [item.get("id") for item in memories if item.get("id")],
            "statuses": [item.get("status") for item in memories if item.get("status")],
        }
    if tool_name == "forget_memory":
        return {
            "lineage_id": payload.get("lineage_id"),
            "forgotten_count": int(payload.get("forgotten_count", 0)),
        }
    if tool_name == "reset_memories":
        return {
            "lineage_count": int(payload.get("lineage_count", 0)),
            "forgotten_count": int(payload.get("forgotten_count", 0)),
        }
    return {}


def memory_trace_outcome(tool_name: str, output: Any) -> dict:
    return {
        "schema_version": 1,
        "kind": "semantic_memory",
        "operation": tool_name,
        "result": "completed",
        **sanitize_memory_result(tool_name, output),
    }


def sanitize_public_memory_event(event: dict) -> dict:
    sanitized = copy.deepcopy(event)
    tool_name = str(sanitized.get("tool") or "")
    if not is_memory_tool(tool_name):
        return sanitized
    if "args" in sanitized:
        sanitized["args"] = sanitize_memory_args(tool_name, sanitized.get("args"))
    if "output" in sanitized:
        summary = sanitize_memory_result(tool_name, sanitized.get("output"))
        sanitized["output"] = json.dumps(summary, sort_keys=True)
    sanitized.pop("draft_preview", None)
    sanitized.pop("references", None)
    return sanitized


def memory_redactions(events: list[dict]) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for event in events:
        if not is_memory_tool(str(event.get("tool") or "")):
            continue
        _collect_sensitive_fields(event.get("args"), replacements)
        _collect_sensitive_fields(_coerce_value(event.get("output")), replacements)
    return replacements


def redact_memory_values(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: redact_memory_values(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_memory_values(item, replacements) for item in value]
    if isinstance(value, str):
        redacted = value
        for sensitive, placeholder in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True
        ):
            redacted = redacted.replace(sensitive, placeholder)
        return redacted
    return value


def _memory_metadata(memory: dict) -> dict:
    return {
        "memory_id": memory.get("id"),
        "memory_type": memory.get("memory_type"),
        "workflow_scope": memory.get("workflow_scope"),
        "has_contact_scope": bool(
            memory.get("has_contact_scope", memory.get("contact_scope"))
        ),
        "status": memory.get("status"),
        "version": memory.get("version"),
    }


def _coerce_mapping(value: Any) -> dict:
    parsed = _coerce_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _coerce_value(value: Any) -> Any:
    if hasattr(value, "content"):
        value = value.content
    if not isinstance(value, str):
        return value
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(value)
        except (ValueError, SyntaxError, TypeError):
            continue
    return value


def _collect_sensitive_fields(value: Any, replacements: dict[str, str]) -> None:
    value = _coerce_value(value)
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "value" and isinstance(item, str) and item:
                replacements[item] = "[REDACTED_MEMORY_VALUE]"
            elif key == "key" and isinstance(item, str) and item:
                replacements[item] = "[REDACTED_MEMORY_KEY]"
            elif key == "contact_scope" and isinstance(item, str) and item:
                replacements[item] = "[REDACTED_MEMORY_CONTACT]"
            else:
                _collect_sensitive_fields(item, replacements)
    elif isinstance(value, list):
        for item in value:
            _collect_sensitive_fields(item, replacements)
