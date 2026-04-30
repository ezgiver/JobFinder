import json
import os
from pathlib import Path

CONFIG_PATH: Path = Path.home() / ".job_finder" / "config.json"


def load_config() -> dict | None:
    """Return parsed config dict, or None if file absent/invalid."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def save_config(config: dict) -> None:
    """Atomically write config dict to CONFIG_PATH."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.replace(tmp_path, CONFIG_PATH)
