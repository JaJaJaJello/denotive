"""Small JSON settings file kept in %APPDATA%\\DeNotive (library location, window state, preferences)."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DeNotive"
CONFIG_FILE = CONFIG_DIR / "settings.json"
DEFAULT_LIBRARY = Path.home() / "Documents" / "DeNotive Library"

DEFAULTS = {
    "library": str(DEFAULT_LIBRARY),
    "snapshot_limit_mb": 50,
    "last_board": None,
    "page_size": "16:9",
    "wheel_zooms": True,
    "show_minimap": True,
    "recent_libraries": [],
}


class Settings(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        self._mine = set()  # keys this window changed
        self.update(self._read())

    @staticmethod
    def _read():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._mine.add(key)

    def save(self):
        """Writes only what this window changed on top of the file, and picks up what another DeNotive window
        changed, so two open windows don't undo each other's settings."""
        disk = self._read()
        for k, v in disk.items():
            if k not in self._mine:
                super().__setitem__(k, v)
        disk.update({k: self[k] for k in self._mine})
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(disk, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass

    def set(self, key, value):
        self[key] = value
        self.save()


settings = Settings()
