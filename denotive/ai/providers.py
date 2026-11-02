"""Model providers behind one small interface.

    provider.stream(system, messages, on_text, should_stop) -> full reply text

messages are [{"role": "user" | "assistant", "text": str, "images": [(mime, base64), ...]}].
Everything here runs on a worker thread and uses only the standard library (no extra packages to bundle).
"""
import glob
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PROVIDERS = [
    ("none", "Off"),
    ("claude_app", "Claude — your Claude subscription (Claude desktop app)"),
    ("anthropic", "Claude — Anthropic API key"),
    ("openai", "ChatGPT models — OpenAI API key"),
    ("gemini", "Gemini — Google AI Studio API key"),
    ("ollama", "Local model — Ollama"),
    ("compatible", "Other OpenAI-compatible server (LM Studio, OpenRouter…)"),
    ("manual", "Copy & paste into any chatbot website"),
]
PROVIDER_NAMES = dict(PROVIDERS)
SHORT_NAMES = {"claude_app": "Claude", "anthropic": "Claude API", "openai": "OpenAI", "gemini": "Gemini",
               "ollama": "Ollama", "compatible": "Custom", "manual": "Copy & paste", "none": "Off"}
NEEDS_KEY = {"anthropic", "openai", "gemini"}
DEFAULT_URLS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1",
                "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
                "ollama": "http://localhost:11434", "compatible": "http://localhost:1234/v1"}
SUGGESTED = {"claude_app": ["sonnet", "opus", "haiku"],
             "anthropic": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
             "openai": ["gpt-5", "gpt-5-mini"], "gemini": ["gemini-2.5-flash", "gemini-2.5-pro"],
             "ollama": ["llama3.2-vision:11b", "qwen2.5:7b", "llama3.2:3b"], "compatible": [], "manual": [],
             "none": []}
CHATBOT_SITES = [("Claude", "https://claude.ai/new"), ("ChatGPT", "https://chatgpt.com/"),
                 ("Gemini", "https://gemini.google.com/app"), ("Don't open a website", "")]
VISION_WORDS = ("vision", "llava", "gemma3", "gemma4", "-vl", "vl:", "qwen2.5vl", "minicpm-v", "moondream", "bakllava",
                "pixtral", "llama4")


class AIError(Exception):
    pass


class NeedsLogin(AIError):
    pass


def role_config(role):
    """Settings for 'coinv' (the Co-Investigator chat) or 'notes' (drafting evidence notes)."""
    from ..settings import settings
    ai = settings.get("ai") or {}
    cfg = dict(ai.get(role) or {})
    if role == "notes" and cfg.get("same", True):
        cfg = dict(ai.get("coinv") or {})
    cfg.setdefault("provider", "none")
    cfg.setdefault("model", "")
    cfg.setdefault("url", "")
    cfg.setdefault("images", True)
    cfg.setdefault("site", "https://claude.ai/new")
    if role == "notes":
        # notes stay plain unless "use the personality for notes too" is ticked
        notes = ai.get("notes") or {}
        cfg["persona"] = (ai.get("coinv") or {}).get("persona", "") if notes.get("persona_too") else ""
    cfg.setdefault("persona", "")
    return cfg


def describe(cfg):
    p = cfg.get("provider", "none")
    if p in ("none", "manual"):
        return SHORT_NAMES[p]
    return f"{SHORT_NAMES.get(p, p)} · {cfg.get('model') or (SUGGESTED.get(p) or ['default'])[0]}"


def make(cfg):
    p = cfg.get("provider", "none")
    cls = {"claude_app": ClaudeApp, "anthropic": Anthropic, "openai": OpenAICompatible, "gemini": OpenAICompatible,
           "compatible": OpenAICompatible, "ollama": Ollama}.get(p)
    if cls is None:
        raise AIError("No AI provider is set up for this. Choose one in Settings → Co-Investigator.")
    return cls(cfg)


# ---------------------------------------------------------------------- HTTP helpers
def _friendly(code, body, host):
    msg = body
    try:
        j = json.loads(body)
        e = j.get("error", j)
        msg = e.get("message") if isinstance(e, dict) else str(e)
    except (ValueError, AttributeError):
        pass
    msg = (msg or "").strip()[:300]
    if code in (401, 403):
        return f"{host} rejected the API key ({code}). Check it in Settings → Co-Investigator.\n{msg}"
    if code == 404:
        return f"{host} doesn't know that model or address (404). Pick another model in Settings.\n{msg}"
    if code == 429:
        return f"{host} says you're over a rate limit or out of credit (429).\n{msg}"
    return f"{host} returned an error ({code}): {msg}"


def _open(url, headers=None, body=None, timeout=300):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"content-type": "application/json", "user-agent": "DeNotive",
                                          **(headers or {})})
    host = urllib.request.urlparse(url).netloc or url
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise AIError(_friendly(e.code, e.read().decode("utf-8", "replace"), host)) from None
    except urllib.error.URLError as e:
        raise AIError(f"Couldn't reach {host}: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise AIError(f"Couldn't reach {host}: {e}") from None


def _lines(resp, should_stop):
    with resp:
        while True:
            if should_stop():
                return
            raw = resp.readline()
            if not raw:
                return
            yield raw.decode("utf-8", "replace").rstrip("\r\n")


def _sse(resp, should_stop):
    """Yields decoded JSON payloads from a server-sent-events stream."""
    for line in _lines(resp, should_stop):
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except ValueError:
            continue


class Provider:
    vision = True

    def __init__(self, cfg):
        self.cfg = cfg
        self.kind = cfg.get("provider")
        self.model = cfg.get("model") or (SUGGESTED.get(self.kind) or [""])[0]
        self.base = (cfg.get("url") or DEFAULT_URLS.get(self.kind, "")).rstrip("/")
        from .keys import get_key
        self.key = get_key(self.kind)
        if self.kind in NEEDS_KEY and not self.key:
            who = {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Google AI Studio"}[self.kind]
            raise AIError(f"Add your {who} API key in Settings → Co-Investigator.")

    def wants_images(self):
        return bool(self.cfg.get("images", True)) and self.vision

    def list_models(self):
        return list(SUGGESTED.get(self.kind, []))


class Anthropic(Provider):
    def _headers(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}

    def stream(self, system, messages, on_text, should_stop, max_tokens=4096):
        msgs = []
        for m in messages:
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
                      for mime, data in (m.get("images") or [])] if self.wants_images() else []
            blocks.append({"type": "text", "text": m["text"]})
            msgs.append({"role": m["role"], "content": blocks})
        body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": msgs, "stream": True}
        out = []
        for ev in _sse(_open(self.base + "/v1/messages", self._headers(), body), should_stop):
            t = ev.get("type")
            if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                piece = ev["delta"].get("text", "")
                out.append(piece)
                on_text(piece)
            elif t == "error":
                raise AIError(ev.get("error", {}).get("message", "Anthropic API error"))
        return "".join(out)

    def list_models(self):
        with _open(self.base + "/v1/models?limit=100", self._headers()) as r:
            return [m["id"] for m in json.loads(r.read()).get("data", [])]


class OpenAICompatible(Provider):
    """OpenAI, Gemini (through Google's OpenAI-compatible endpoint), LM Studio, OpenRouter, vLLM, …"""

    def _headers(self):
        return {"authorization": f"Bearer {self.key}"} if self.key else {}

    def stream(self, system, messages, on_text, should_stop, max_tokens=None):
        msgs = [{"role": "system", "content": system}]
        for m in messages:
            imgs = (m.get("images") or []) if self.wants_images() else []
            if imgs and m["role"] == "user":
                content = [{"type": "text", "text": m["text"]}] + [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}} for mime, data in imgs]
            else:
                content = m["text"]
            msgs.append({"role": m["role"], "content": content})
        body = {"model": self.model, "messages": msgs, "stream": True}
        out = []
        for ev in _sse(_open(self.base + "/chat/completions", self._headers(), body), should_stop):
            if ev.get("error"):
                err = ev["error"]
                raise AIError(err.get("message") if isinstance(err, dict) else str(err))
            for ch in ev.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content") or ""
                if piece:
                    out.append(piece)
                    on_text(piece)
        return "".join(out)

    def list_models(self):
        with _open(self.base + "/models", self._headers()) as r:
            ids = [str(m.get("id", "")).replace("models/", "") for m in json.loads(r.read()).get("data", [])]
        return sorted(i for i in ids if i)


class Ollama(Provider):
    def __init__(self, cfg):
        super().__init__(cfg)
        name = self.model.lower()
        self.vision = any(w in name for w in VISION_WORDS)

    def stream(self, system, messages, on_text, should_stop, max_tokens=None):
        msgs = [{"role": "system", "content": system}]
        for m in messages:
            entry = {"role": m["role"], "content": m["text"]}
            if self.wants_images() and m.get("images"):
                entry["images"] = [data for _, data in m["images"]]
            msgs.append(entry)
        body = {"model": self.model, "messages": msgs, "stream": True,
                "options": {"num_ctx": int(self.cfg.get("num_ctx", 16384))}}
        out = []
        try:
            resp = _open(self.base + "/api/chat", None, body)
        except AIError as e:
            if "refused" in str(e).lower() or "10061" in str(e):
                raise AIError("Ollama isn't running. Start the Ollama app (or run “ollama serve”) and try again.")
            raise
        for line in _lines(resp, should_stop):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise AIError(f"Ollama: {ev['error']}")
            piece = (ev.get("message") or {}).get("content") or ""
            if piece:
                out.append(piece)
                on_text(piece)
            if ev.get("done"):
                break
        return "".join(out)

    def list_models(self):
        try:
            with _open(self.base + "/api/tags", timeout=5) as r:
                names = sorted(m["name"] for m in json.loads(r.read()).get("models", []))
            return [n for n in names if "embed" not in n.lower()]  # embedding models can't chat
        except AIError:
            raise AIError("Ollama isn't running. Start the Ollama app (or run “ollama serve”) and try again.")


# ---------------------------------------------------------------------- Claude desktop app (subscription)
def find_claude(override=""):
    """The Claude Code command-line tool that ships with the Claude desktop app (newest copy wins)."""
    if override and Path(override).is_file():
        return override
    la, ra = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
    patterns = [os.path.join(la, "Claude-Data", "*", "claude-code", "**", "claude.exe"),
                os.path.join(ra, "Claude", "claude-code", "**", "claude.exe"),
                os.path.join(la, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code", "**",
                             "claude.exe")]
    found = [p for pat in patterns for p in glob.glob(pat, recursive=True)]
    if found:
        return max(found, key=os.path.getmtime)
    return shutil.which("claude") or ""


def _workdir():
    from ..settings import CONFIG_DIR
    d = CONFIG_DIR / "claude-work"  # an empty folder, so no project instructions get picked up
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


class ClaudeApp(Provider):
    """Runs Claude through the Claude desktop app's command-line tool, using the user's own sign-in.
    Tools are switched off: Claude only reads what DeNotive hands it and writes a reply."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.exe = find_claude(cfg.get("exe", ""))
        if not self.exe:
            raise AIError("Couldn't find Claude's command-line tool. Install or update the Claude desktop app, "
                          "or choose another provider in Settings → Co-Investigator.")

    def stream(self, system, messages, on_text, should_stop, max_tokens=None):
        # one-shot run: earlier turns travel inside the single user message
        *history, last = messages
        text = last["text"]
        if history:
            past = "\n\n".join(f"{'Researcher' if m['role'] == 'user' else 'Co-Investigator'}: {m['text']}"
                               for m in history)
            text = f"Earlier in this conversation:\n\n{past}\n\n---\n\n{text}"
        images = [im for m in messages for im in (m.get("images") or [])] if self.wants_images() else []
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
                  for mime, data in images] + [{"type": "text", "text": text}]
        cmd = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
               "--system-prompt", system]
        if self.model and self.model != "default":
            cmd += ["--model", self.model]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", cwd=_workdir(),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise AIError(f"Couldn't start Claude: {e}")
        try:
            proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": blocks}}) + "\n")
            proc.stdin.close()
        except OSError:
            pass
        out, final, error = [], None, None
        for line in proc.stdout:
            if should_stop():
                proc.kill()
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "stream_event":
                e = ev.get("event", {})
                if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                    piece = e["delta"].get("text", "")
                    out.append(piece)
                    on_text(piece)
            elif t == "result" or "result" in ev:
                final = str(ev.get("result") or "")
                if ev.get("is_error"):
                    error = final or "Claude reported an error"
        proc.wait()
        text = "".join(out) or (final or "")
        msg = error or ("" if text else (proc.stderr.read() or "").strip())
        if msg or not text:
            msg = msg or "Claude returned an empty reply."
            if "not logged in" in msg.lower() or "/login" in msg or "sign in" in msg.lower():
                raise NeedsLogin("Claude's command-line tool isn't signed in yet. Use “Sign in to Claude…” in "
                                 "Settings → Co-Investigator, then try again.")
            raise AIError(msg[:600])
        return text

    def list_models(self):
        return ["haiku", "sonnet", "opus"]

    def login(self):
        claude_login(self.exe)


def claude_login(exe):
    """Opens a console window running Claude's one-time sign-in."""
    subprocess.Popen([exe, "auth", "login"], cwd=_workdir(), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
