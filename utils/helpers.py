"""Helper functions for the human evaluation app."""

import yaml
from pathlib import Path

# Project root: directory containing config.yaml (one level up from utils/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CONFIGS_DIR = PROJECT_ROOT / "configs"
ACTIVE_FILE = CONFIGS_DIR / ".active"


def get_local_audio_path(relative_path: str) -> Path:
    """Resolve a config path (e.g. audio/my_file.wav) to an absolute path under the project.
    Uses Path(__file__) so it works when run from any cwd (e.g. Streamlit Cloud)."""
    path = (relative_path or "").strip()
    if not path:
        return PROJECT_ROOT
    return (PROJECT_ROOT / path).resolve()


def _ensure_configs_dir() -> None:
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)


def get_active_config_filename() -> str | None:
    """Return the active config filename (e.g. phase1.yaml) or None if not set."""
    if not ACTIVE_FILE.exists():
        return None
    name = ACTIVE_FILE.read_text().strip()
    return name if name else None


def get_active_config_path() -> Path | None:
    """Return full path to the active config file, or None."""
    name = get_active_config_filename()
    if not name:
        return None
    path = CONFIGS_DIR / name
    return path if path.exists() else None


def set_active_config(filename: str) -> None:
    """Set the active config by filename (e.g. phase1.yaml)."""
    _ensure_configs_dir()
    ACTIVE_FILE.write_text(filename.strip())


def list_config_files() -> list[str]:
    """Return sorted list of .yaml filenames in configs/ (excluding .active)."""
    if not CONFIGS_DIR.exists():
        return []
    return sorted(
        p.name for p in CONFIGS_DIR.iterdir()
        if p.suffix.lower() in (".yaml", ".yml") and p.name.startswith(".") is False
    )


def load_config() -> dict:
    """Load the active config: from configs/<active> if set, else from config.yaml."""
    active_path = get_active_config_path()
    if active_path is not None:
        with open(active_path, "r") as f:
            return yaml.safe_load(f)
    # Backwards compatibility: use root config.yaml
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f)
    raise FileNotFoundError("No config found. Save a config from Admin or add config.yaml.")


def save_config(config: dict) -> None:
    """Save config to the active config file (or config.yaml if no active set)."""
    active_path = get_active_config_path()
    path = active_path if active_path is not None else CONFIG_PATH
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def save_config_as(config: dict, filename: str) -> Path:
    """Save config to configs/<filename>.yaml. Adds .yaml if missing. Sets config_name to filename stem.
    Returns the path saved to."""
    _ensure_configs_dir()
    filename = filename.strip()
    if not filename:
        raise ValueError("Filename is required")
    if not filename.lower().endswith((".yaml", ".yml")):
        filename = f"{filename}.yaml"
    config = dict(config)
    config["config_name"] = Path(filename).stem
    path = CONFIGS_DIR / filename
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    return path
