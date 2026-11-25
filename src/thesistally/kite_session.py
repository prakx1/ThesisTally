"""Read-only Kite status and browser handoff to an operator's login helper."""

import os
import socket
import webbrowser
from urllib.parse import urlsplit

from . import market

DEFAULT_LOGIN_URL = "http://127.0.0.1:8000/login"
CONNECTED = "Connected"


def check_status() -> str:
    """Verify the current credentials without exposing account details."""
    try:
        with market.Feed(timeout=5) as feed:
            feed.session(attempts=1)
    except market.KiteCredentialsMissing:
        return "Session not configured"
    except market.KiteLoginRequired:
        return "Login required"
    except market.MarketError:
        return "Status unavailable"
    return CONNECTED


def open_login() -> str:
    """Open the configured login helper; the browser handles authentication."""
    url = os.getenv("RADAR_KITE_LOGIN_URL", DEFAULT_LOGIN_URL)
    try:
        target = urlsplit(url)
        host = target.hostname
        port = target.port
        local = host in ("localhost", "127.0.0.1", "::1")
        if (
            not host
            or target.username is not None
            or any(character.isspace() or not character.isprintable() for character in url)
            or (target.scheme != "https" and not (local and target.scheme == "http"))
        ):
            raise ValueError()
    except ValueError:
        raise market.MarketError(
            "Set RADAR_KITE_LOGIN_URL to an HTTPS login helper or a loopback HTTP URL."
        ) from None

    if local:
        address = (host, port if port is not None else (443 if target.scheme == "https" else 80))
        try:
            with socket.create_connection(address, timeout=1):
                pass
        except OSError:
            raise market.MarketError("Start your local Kite login helper and try again.") from None
    try:
        opened = webbrowser.open_new_tab(url)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        instruction = (
            f"Open {DEFAULT_LOGIN_URL} in your browser."
            if url == DEFAULT_LOGIN_URL
            else "Open the helper configured in RADAR_KITE_LOGIN_URL in your browser."
        )
        raise market.MarketError(instruction)
    return "Finish signing in with Zerodha in your browser, then check status."
