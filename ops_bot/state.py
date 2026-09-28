from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read state file %s", self.path)
            return {}

    def save(self) -> None:
        # Merge with on-disk state so external writers (e.g. deploy notify script) are not
        # clobbered when this process saves its in-memory cursor keys.
        disk = self._load() if self.path.exists() else {}
        merged = {**disk, **self._data}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._data = merged

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._data and self.path.exists():
            try:
                disk = self._load()
                if key in disk:
                    self._data[key] = disk[key]
            except Exception:
                pass
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()
