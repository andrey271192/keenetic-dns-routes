import json
import uuid
from pathlib import Path
from typing import Any

from . import config


def _default_store() -> dict[str, Any]:
    return {
        "groups": {
            "US": {"interface_id": "", "lines": []},
            "RU": {"interface_id": "", "lines": []},
        },
        "routers": [],
    }


def ensure_store() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not config.STORE_FILE.exists():
        config.STORE_FILE.write_text(
            json.dumps(_default_store(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_store() -> dict[str, Any]:
    ensure_store()
    try:
        return json.loads(config.STORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _default_store()


def save_store(data: dict[str, Any]) -> None:
    ensure_store()
    config.STORE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def new_router_id() -> str:
    return str(uuid.uuid4())[:8]
