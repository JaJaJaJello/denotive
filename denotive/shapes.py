"""Predict Shapes: a picture on a slide redrawn as editable pieces.

The work is done by the DeNotive Vectorizer (the trainer folder next to the app's code), run with a Python that
has PyTorch: the trained model and the tracing need packages the app itself doesn't carry. The pieces come from
the AI chosen in Settings → Co-Investigator → Image to shape prediction, or from the built-in trained model; if the
AI can't answer (signed out, usage limit, no usable reply) the built-in model steps in. Lines, areas and frames
are always traced from the picture's own pixels.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .settings import settings

KEY = "shape_prediction"
NEEDS = "import torch, numpy, cv2, scipy, PIL, PySide6"


def default():
    """Claude Opus until something else is picked: through the Anthropic API if that's how the Co-Investigator
    reaches Claude, otherwise through the Claude desktop app. (If Claude can't answer, the built-in model does.)"""
    coinv = ((settings.get("ai") or {}).get("coinv") or {}).get("provider")
    if coinv == "anthropic":
        return {"engine": "ai", "provider": "anthropic", "model": "claude-opus-5-5"}
    return {"engine": "ai", "provider": "claude_app", "model": "opus"}


def config():
    cfg = dict(settings.get(KEY) or {})
    if "engine" not in cfg:
        cfg.update(default())
    cfg.setdefault("provider", "")
    cfg.setdefault("model", "")
    return cfg


def describe(cfg=None):
    from .ai.providers import SHORT_NAMES
    cfg = cfg or config()
    if cfg.get("engine") != "ai" or not cfg.get("provider"):
        return "the built-in model"
    return f"{SHORT_NAMES.get(cfg['provider'], cfg['provider'])} · {cfg.get('model') or 'default'}"


def find_trainer():
    """The Vectorizer's folder: set in the settings, else beside the app's code (or above DeNotive.exe)."""
    own = (settings.get(KEY) or {}).get("trainer")
    places = [Path(own)] if own else []
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    places += [p / "trainer" for p in list(here.parents)[:5]]
    for p in places:
        if (p / "main.py").is_file() and (p / "vectorizer" / "shapes.py").is_file():
            return p
    return None


def checkpoint(trainer):
    p = trainer / "runs" / "default" / "best.pt" if trainer else None
    return p if p and p.is_file() else None


def _python_candidates():
    own = (settings.get(KEY) or {}).get("python")
    out = [own] if own else []
    if not getattr(sys, "frozen", False):
        out.append(sys.executable)
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:  # the Microsoft Store stub just opens the Store
            out.append(p)
    py = shutil.which("py")
    if py:
        out.append(py)
    seen, uniq = set(), []
    for p in out:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            uniq.append(p)
    return uniq


_found_python = None


def find_python(refresh=False):
    """A Python that can run the Vectorizer (has PyTorch and the rest). Takes a few seconds the first time."""
    global _found_python
    if _found_python and not refresh:
        return _found_python
    for exe in _python_candidates():
        try:
            r = subprocess.run([exe, "-c", NEEDS], capture_output=True, timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            _found_python = exe
            return exe
    return None


def readiness():
    """(ready, message) for the built-in model, for the settings window and error messages."""
    t = find_trainer()
    if t is None:
        return False, ("The DeNotive Vectorizer (the trainer folder) wasn't found next to DeNotive, so neither the "
                       "built-in model nor the tracing can run.")
    ck = checkpoint(t)
    py = find_python()
    if py is None:
        return False, (f"Found the Vectorizer in {t}, but no Python with PyTorch, OpenCV, SciPy and PySide6 to run "
                       "it. Install them with:  python -m pip install torch opencv-python scipy PySide6-Essentials pillow")
    if ck is None:
        return False, f"Found the Vectorizer in {t}, but it has no trained model yet (runs\\default\\best.pt)."
    return True, f"Ready: {t}\nRuns with {py}"


class Prediction(QObject):
    """One picture through the Vectorizer in the background. status(text), done(result dict), failed(message)."""
    status = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, image_path, target, cfg=None):
        super().__init__()
        self.image_path = str(image_path)
        self.target = target
        self.cfg = cfg or config()
        self.proc = None
        self.cancelled = False
        self.tmp = Path(tempfile.mkdtemp(prefix="denotive-shapes-"))

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()

    def _run(self):
        trainer = find_trainer()
        py = find_python() if trainer else None
        if trainer is None or py is None:
            self.failed.emit(readiness()[1])
            return
        out = self.tmp / "result.json"
        cmd = [py, str(trainer / "main.py"), "shapes", self.image_path, "--out", str(out),
               "--target", *(f"{v:.3f}" for v in self.target)]
        if self.cfg.get("engine") == "ai" and self.cfg.get("provider"):
            cmd += ["--engine", "ai", "--provider", self.cfg["provider"], "--model", self.cfg.get("model", "")]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        err = []
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                         encoding="utf-8", errors="replace", cwd=str(trainer), env=env,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            self.failed.emit(f"Couldn't start the Vectorizer: {e}")
            return
        threading.Thread(target=lambda: err.extend(self.proc.stderr), daemon=True).start()
        for line in self.proc.stdout:
            if line.startswith("STATUS "):
                self.status.emit(line[7:].strip())
        self.proc.wait()
        if self.cancelled:
            return
        if self.proc.returncode != 0 or not out.exists():
            tail = "".join(err).strip().splitlines()
            self.failed.emit("The Vectorizer stopped with an error:\n" + "\n".join(tail[-6:]) if tail else
                             "The Vectorizer stopped without a result.")
            return
        try:
            self.done.emit(json.loads(out.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            self.failed.emit(f"The Vectorizer's result couldn't be read: {e}")

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
