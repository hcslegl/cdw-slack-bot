import os
import json

_RUNTIME_COOKIES = None
_CACHE_FILE = "/tmp/runtime_cookies.json"


def get_cookies() -> list | None:
    """Return cookies from runtime cache, file cache, or env var — in that order."""
    global _RUNTIME_COOKIES

    if _RUNTIME_COOKIES is not None:
        return _RUNTIME_COOKIES

    try:
        with open(_CACHE_FILE) as f:
            _RUNTIME_COOKIES = json.load(f)
            return _RUNTIME_COOKIES
    except Exception:
        pass

    raw = os.environ.get("CDW_COOKIES", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass

    return None


def set_cookies(cookies_json_str: str) -> list:
    """Validate, store, and cache new cookies. Raises ValueError if JSON is invalid."""
    global _RUNTIME_COOKIES

    try:
        cookies = json.loads(cookies_json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    if not isinstance(cookies, list):
        raise ValueError("Expected a JSON array of cookies.")

    _RUNTIME_COOKIES = cookies

    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(cookies, f)
    except Exception:
        pass

    return cookies
