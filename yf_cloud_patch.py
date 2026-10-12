"""
yf_cloud_patch.py
-----------------
Monkey-patches yfinance to use a browser-like requests Session so that
Yahoo Finance does not reject requests from cloud server IPs.

Import this module ONCE at startup (hub_web.py does it automatically).
It is safe to import multiple times; the patch is applied only once.
"""

import requests

_PATCHED = False


def apply():
    """Inject a browser-like session into yfinance. Call once at start-up."""
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })

    # ---- patch yfinance.data (used by Ticker and download) ----
    try:
        import yfinance.data as _yfdata
        # YfData stores the session on the class; override it
        if hasattr(_yfdata, "YfData"):
            _yfdata.YfData._session = session
    except Exception:
        pass

    # ---- older yfinance versions keep session on yfinance.base ----
    try:
        import yfinance.base as _yfbase
        if hasattr(_yfbase, "TickerBase"):
            _yfbase.TickerBase._session = session
    except Exception:
        pass

    # ---- patch the shared requests module yfinance imports ----
    try:
        import yfinance.utils as _yfutils
        _yfutils.requests = session  # type: ignore[attr-defined]
    except Exception:
        pass

    # ---- fallback: patch the requests module yfinance.data imported ----
    try:
        import yfinance.data as _yfdata2
        import types
        # Wrap the module-level requests so every .get/.post uses our session
        class _SessionWrapper(types.ModuleType):
            def __getattr__(self, name):
                return getattr(session, name) if hasattr(session, name) else getattr(requests, name)
        if hasattr(_yfdata2, "requests"):
            _yfdata2.requests = _SessionWrapper("requests")  # type: ignore[attr-defined]
    except Exception:
        pass

    print("[yf_cloud_patch] yfinance session patched with browser-like headers.")
