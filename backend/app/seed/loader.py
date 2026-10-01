"""Load deterministic email fixtures into SQLite (+ Chroma) for dev and eval.

Dev fixtures land in the main database so the UI and existing agent tools
see them like real Outlook emails. Eval fixtures land in an isolated
SQLite file + Chroma collection so eval runs never touch business data.
"""

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.core.config import Config
from app.models.email import DraftReply, Email, LabelAction, SentAction
from app.services.database import DatabaseService
from app.services.memory import MemoryService

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent

Kind = Literal["dev", "eval"]

# Newest email in the shipped dev fixture. `--anchor` shifts every date in the
# fixture relative to this reference so the top of the inbox lines up with the
# requested date.
FIXTURE_ANCHOR_DATE = date(2026, 9, 15)
# Only dates inside this window are considered part of the fictional timeline
# and shifted. Future event dates (e.g. the Feb 2027 CFP) are left alone.
_FIXTURE_WINDOW = (date(2026, 8, 1), date(2026, 11, 15))

_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12,
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "Jun": 6, "Jul": 7,
    "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_MONTHS_ABBREV = {"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"}
_WEEKDAYS_ABBREV = {"Mon","Tue","Wed","Thu","Fri","Sat","Sun"}

_ISO_DATE_RE = re.compile(r"\b(2026)-(\d{2})-(\d{2})\b")
_NATURAL_DATE_RE = re.compile(
    r"\b"
    r"(?:(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    r"|Mon|Tue|Wed|Thu|Fri|Sat|Sun)(,)?\s+)?"
    r"(January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(\d{1,2})"
    r"(?:(,)\s+(2026))?"
    r"\b"
)


def _shift_iso(match: re.Match, delta_days: int) -> str:
    try:
        orig = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return match.group(0)
    if not (_FIXTURE_WINDOW[0] <= orig <= _FIXTURE_WINDOW[1]):
        return match.group(0)
    return (orig + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def _shift_natural(match: re.Match, delta_days: int) -> str:
    wd_txt, wd_comma, month_txt, day_txt, year_comma, year_txt = match.groups()
    if year_txt and year_txt != "2026":
        return match.group(0)
    try:
        orig = date(2026, _MONTHS[month_txt], int(day_txt))
    except (ValueError, KeyError):
        return match.group(0)
    if not (_FIXTURE_WINDOW[0] <= orig <= _FIXTURE_WINDOW[1]):
        return match.group(0)
    shifted = orig + timedelta(days=delta_days)
    month_out = shifted.strftime("%b") if month_txt in _MONTHS_ABBREV else shifted.strftime("%B")
    day_out = str(shifted.day)
    parts: list[str] = []
    if wd_txt:
        wd_str = shifted.strftime("%a") if wd_txt in _WEEKDAYS_ABBREV else shifted.strftime("%A")
        parts.append(wd_str + (wd_comma or ""))
    parts.append(month_out)
    parts.append(day_out + (year_comma or ""))
    if year_txt:
        parts.append(str(shifted.year))
    return " ".join(parts)


def _shift_text(text: str, delta_days: int) -> str:
    text = _ISO_DATE_RE.sub(lambda m: _shift_iso(m, delta_days), text)
    text = _NATURAL_DATE_RE.sub(lambda m: _shift_natural(m, delta_days), text)
    return text


def _shift_email_dates(email: dict, delta_days: int) -> None:
    """Mutate one fixture email's received_datetime and text fields in place."""
    dt = datetime.fromisoformat(email["received_datetime"].replace("Z", "+00:00"))
    email["received_datetime"] = (dt + timedelta(days=delta_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for field in ("subject", "body_preview", "body_content", "body_html"):
        v = email.get(field)
        if isinstance(v, str):
            email[field] = _shift_text(v, delta_days)


def _resolve_anchor(anchor_arg: str, kind: Kind) -> date:
    """Parse --anchor. Accepts 'auto' or ISO YYYY-MM-DD. Only valid for dev fixtures.

    Enforces multiple-of-7 shift so weekday-labeled references (e.g. 'Wed Sep 23')
    stay on the correct weekday after the shift.
    """
    if kind != "dev":
        raise ValueError("--anchor only applies to --kind dev")
    if anchor_arg == "auto":
        today = date.today()
        days_back = (today.weekday() - FIXTURE_ANCHOR_DATE.weekday()) % 7
        return today - timedelta(days=days_back)
    try:
        anchor = date.fromisoformat(anchor_arg)
    except ValueError as ex:
        raise ValueError(f"Invalid --anchor value {anchor_arg!r}: expected YYYY-MM-DD or 'auto'") from ex
    delta = (anchor - FIXTURE_ANCHOR_DATE).days
    if delta % 7 != 0:
        # Compute nearest valid anchors so the error is actionable.
        wd_diff = (anchor.weekday() - FIXTURE_ANCHOR_DATE.weekday()) % 7
        near_back = anchor - timedelta(days=wd_diff)
        near_fwd = anchor + timedelta(days=(7 - wd_diff) % 7 or 7)
        raise ValueError(
            f"--anchor {anchor} is a {anchor.strftime('%A')}, which does not preserve "
            f"the fixture's {FIXTURE_ANCHOR_DATE.strftime('%A')} weekday alignment. "
            f"Nearest valid dates: {near_back} or {near_fwd}."
        )
    return anchor

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


def _reset_domain_state(db: DatabaseService, kind: Kind) -> dict:
    """Clear fixture-derived label_actions, draft_replies, and sent_actions.

    Plain --kind dev only wipes Email rows and lets rows that reference them
    (mark_read history from previous takes, drafts the user wrote, dry-run
    sent replies) go orphaned. --reset also cleans those so a demo recording
    starts from a truly identical state each time.
    """
    prefix = f"{kind}-%"
    session = db.Session()
    try:
        la = (
            session.query(LabelAction)
            .filter(LabelAction.email_id.like(prefix))
            .delete(synchronize_session=False)
        )
        dr = (
            session.query(DraftReply)
            .filter(DraftReply.email_id.like(prefix))
            .delete(synchronize_session=False)
        )
        sa = (
            session.query(SentAction)
            .filter(SentAction.original_email_id.like(prefix))
            .delete(synchronize_session=False)
        )
        session.commit()
        counts = {"label_actions": la, "draft_replies": dr, "sent_actions": sa}
        logger.info(
            f"Reset domain state for prefix '{kind}-': "
            f"label_actions={la}, draft_replies={dr}, sent_actions={sa}"
        )
        return counts
    finally:
        session.close()


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


def load_fixtures(
    kind: Kind,
    wipe: bool = True,
    verify: bool = False,
    reset: bool = False,
    anchor: str | None = None,
) -> dict:
    if kind not in TARGETS:
        raise ValueError(f"Unknown kind: {kind!r}. Must be one of {list(TARGETS)}")

    fixture_path = SEED_DIR / f"{kind}_emails.json"
    with open(fixture_path) as f:
        emails = json.load(f)

    anchor_date: date | None = None
    delta_days = 0
    if anchor is not None:
        anchor_date = _resolve_anchor(anchor, kind)
        delta_days = (anchor_date - FIXTURE_ANCHOR_DATE).days
        for e in emails:
            _shift_email_dates(e, delta_days)
        logger.info(
            f"Anchored fixture to {anchor_date} (shift={delta_days:+d} days from {FIXTURE_ANCHOR_DATE})"
        )

    target = TARGETS[kind]
    db = DatabaseService(db_path=target["db_path"])

    mem: MemoryService | None = None
    try:
        mem = MemoryService(
            collection_name=target["chroma_collection"],
            persist_subdir=target["chroma_subdir"],
        )
    except ValueError as ex:
        # Missing GOOGLE_API_KEY (Gemini embedding init). Dev tolerates it,
        # UI still works from SQLite. Eval requires it, RAG is in scope.
        if kind == "dev":
            logger.warning(
                f"Chroma indexing skipped ({ex}); loading dev fixtures into SQLite only"
            )
        else:
            raise

    reset_counts: dict | None = None
    if reset:
        reset_counts = _reset_domain_state(db, kind)

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

    result = {"kind": kind, "inserted": inserted, "indexed": indexed}
    if reset_counts is not None:
        result["reset"] = reset_counts
    if anchor_date is not None:
        result["anchor"] = {"date": anchor_date.isoformat(), "shift_days": delta_days}
    return result


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
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Also delete label_actions, draft_replies, and sent_actions rows "
            "that reference fixture emails. Use between demo takes to restore "
            "a truly identical starting state."
        ),
    )
    parser.add_argument(
        "--anchor",
        default=None,
        help=(
            "Shift every date in the dev fixture so the newest email lines up with "
            "this date. Pass 'auto' to pick the most recent past date matching the "
            "fixture's weekday (usually 1 to 6 days ago). Only accepts shifts that "
            "are a multiple of 7 days so weekday-labeled references stay correct. "
            "Applies to --kind dev only."
        ),
    )
    args = parser.parse_args()

    try:
        result = load_fixtures(
            kind=args.kind,
            wipe=not args.no_wipe,
            verify=args.verify,
            reset=args.reset,
            anchor=args.anchor,
        )
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
