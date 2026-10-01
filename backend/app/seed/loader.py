"""Load deterministic email fixtures into SQLite (+ Chroma) for dev and eval.

Dev fixtures land in the main database so the UI and existing agent tools
see them like real Outlook emails. Eval fixtures land in an isolated
SQLite file + Chroma collection so eval runs never touch business data.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.core.config import Config
from app.models.email import Email
from app.services.database import DatabaseService
from app.services.memory import MemoryService

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent

Kind = Literal["dev", "eval"]

TARGETS = {
    "dev": {
        "db_path": None,  # DatabaseService default = data/emails.db
        "chroma_collection": "email_context",
        "chroma_subdir": "vector_db",
    },
    "eval": {
        "db_path": str(Config.DATA_DIR / "eval.db"),
        "chroma_collection": "email_context_eval",
        "chroma_subdir": "vector_db_eval",
    },
}


def _to_row(e: dict, kind: Kind) -> dict:
    dt = datetime.fromisoformat(e["received_datetime"].replace("Z", "+00:00"))
    md = {"category": e.get("category"), "fixture_kind": kind}
    if e.get("expected_agent_action"):
        md["expected_agent_action"] = e["expected_agent_action"]
    return {
        "id": e["id"],
        "subject": e["subject"],
        "sender_name": e.get("sender_name"),
        "sender_email": e["sender_email"],
        "received_datetime": int(dt.timestamp()),
        "body_preview": e.get("body_preview"),
        "body_content": e.get("body_content"),
        "body_html": e.get("body_html"),
        "is_read": e.get("is_read", False),
        "has_attachments": e.get("has_attachments", False),
        "attachments": e.get("attachments"),
        "metadata_json": md,
    }


def _chroma_metadata(e: dict, kind: Kind) -> dict:
    # Chroma metadata must be primitives — flatten only what RAG filters need.
    return {
        "subject": e["subject"],
        "sender": e["sender_email"],
        "category": e.get("category") or "unknown",
        "received": e["received_datetime"],
        "fixture_kind": kind,
    }


def _wipe(db: DatabaseService, mem: MemoryService | None, kind: Kind) -> None:
    prefix = f"{kind}-"
    session = db.Session()
    try:
        deleted = (
            session.query(Email)
            .filter(Email.id.like(f"{prefix}%"))
            .delete(synchronize_session=False)
        )
        session.commit()
        logger.info(f"Wiped {deleted} SQLite rows with prefix '{prefix}'")
    finally:
        session.close()

    if mem is not None:
        # Delete by fixture_kind so we never touch real Outlook vectors even
        # if some unrelated id-collision ever occurred.
        try:
            existing = mem.collection.get(where={"fixture_kind": kind})
            ids = existing.get("ids") or []
            if ids:
                mem.collection.delete(ids=ids)
                logger.info(f"Wiped {len(ids)} Chroma vectors (fixture_kind={kind})")
        except Exception as ex:
            logger.warning(f"Chroma wipe skipped: {ex}")


def _verify(db: DatabaseService, kind: Kind, expected: list[dict]) -> None:
    prefix = f"{kind}-"
    session = db.Session()
    try:
        rows = session.query(Email).filter(Email.id.like(f"{prefix}%")).all()
    finally:
        session.close()
    ids_db = sorted(r.id for r in rows)
    ids_fx = sorted(e["id"] for e in expected)
    if ids_db != ids_fx:
        missing = set(ids_fx) - set(ids_db)
        extra = set(ids_db) - set(ids_fx)
        raise AssertionError(
            f"Verify failed for kind={kind}. missing={sorted(missing)} extra={sorted(extra)}"
        )
    logger.info(f"Verify OK: {len(rows)} '{prefix}*' rows match fixture exactly")


def load_fixtures(kind: Kind, wipe: bool = True, verify: bool = False) -> dict:
    if kind not in TARGETS:
        raise ValueError(f"Unknown kind: {kind!r}. Must be one of {list(TARGETS)}")

    fixture_path = SEED_DIR / f"{kind}_emails.json"
    with open(fixture_path) as f:
        emails = json.load(f)

    target = TARGETS[kind]
    db = DatabaseService(db_path=target["db_path"])

    mem: MemoryService | None = None
    try:
        mem = MemoryService(
            collection_name=target["chroma_collection"],
            persist_subdir=target["chroma_subdir"],
        )
    except ValueError as ex:
        # Missing GOOGLE_API_KEY (Gemini embedding init). Dev tolerates it —
        # UI still works from SQLite. Eval requires it — RAG is in scope.
        if kind == "dev":
            logger.warning(
                f"Chroma indexing skipped ({ex}); loading dev fixtures into SQLite only"
            )
        else:
            raise

    if wipe:
        _wipe(db, mem, kind)

    inserted = 0
    indexed = 0
    for e in emails:
        row = _to_row(e, kind)
        db.insert_email(row)
        inserted += 1
        if mem is not None and row.get("body_content"):
            mem.add_email(
                email_id=row["id"],
                text=f"{row['subject']}. {row['body_content']}",
                metadata=_chroma_metadata(e, kind),
            )
            indexed += 1

    logger.info(
        f"Loaded kind={kind} inserted={inserted} indexed={indexed} "
        f"db={target['db_path'] or 'default'} collection={target['chroma_collection']}"
    )

    if verify:
        _verify(db, kind, emails)

    return {"kind": kind, "inserted": inserted, "indexed": indexed}


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Load FeedFlux email fixtures for dev or eval."
    )
    parser.add_argument("--kind", choices=["dev", "eval"], required=True)
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Do not delete existing fixture rows before loading (default: wipe by id prefix).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="After load, assert DB row set equals fixture id set.",
    )
    args = parser.parse_args()

    try:
        result = load_fixtures(kind=args.kind, wipe=not args.no_wipe, verify=args.verify)
        print(json.dumps(result))
        return 0
    except AssertionError as e:
        logger.error(f"Verify failed: {e}")
        return 2
    except Exception as e:
        logger.error(f"Load failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
