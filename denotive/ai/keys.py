"""API keys are stored encrypted with Windows DPAPI (readable only by this Windows user on this PC)."""
import base64
import ctypes
import os
from ctypes import wintypes

ENV_VARS = {"anthropic": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "compatible": ("OPENAI_COMPATIBLE_API_KEY",)}


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x01, ctypes.byref(out)):  # UI_FORBIDDEN
        raise OSError("Windows could not encrypt/decrypt the key")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def seal(secret: str) -> str:
    if not secret:
        return ""
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode()
    return "b64:" + base64.b64encode(secret.encode("utf-8")).decode()


def unseal(token: str) -> str:
    if not token:
        return ""
    try:
        kind, data = token.split(":", 1)
        raw = base64.b64decode(data)
        if kind == "dpapi":
            return _dpapi(raw, False).decode("utf-8")
        return raw.decode("utf-8")
    except (ValueError, OSError):
        return ""


def get_key(provider):
    from ..settings import settings
    k = unseal((settings.get("ai_keys") or {}).get(provider, ""))
    if k:
        return k
    for var in ENV_VARS.get(provider, ()):
        if os.environ.get(var):
            return os.environ[var]
    return ""


def set_key(provider, secret):
    from ..settings import settings
    keys = dict(settings.get("ai_keys") or {})
    if secret:
        keys[provider] = seal(secret)
    else:
        keys.pop(provider, None)
    settings.set("ai_keys", keys)
