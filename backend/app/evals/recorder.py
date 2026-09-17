import json
from pathlib import Path
from typing import Any


class TrialRecorder:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as output_file:
            output_file.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
                + "\n"
            )
