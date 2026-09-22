from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def open_agent_checkpointer(path: str | Path):
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        yield checkpointer
