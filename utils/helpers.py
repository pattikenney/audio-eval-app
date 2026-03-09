"""Helper functions for the human evaluation app."""

import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_local_audio_path(relative_path: str) -> Path:
    """Resolve a config path (e.g. audio/my_file.wav) to an absolute path under the project."""
    path = (relative_path or "").strip()
    if not path:
        return PROJECT_ROOT
    return (PROJECT_ROOT / path).resolve()


def load_config() -> dict:
    """Load config from config.yaml."""
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    """Save config to config.yaml."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
