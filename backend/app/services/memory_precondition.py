import hashlib
import json


TARGET_FINGERPRINT_FIELDS = (
    "id",
    "lineage_id",
    "version",
    "profile_id",
    "memory_type",
    "workflow_scope",
    "contact_scope",
    "key",
    "value",
    "status",
)


def memory_target_fingerprint(memory: dict) -> str:
    snapshot = {field: memory.get(field) for field in TARGET_FINGERPRINT_FIELDS}
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
