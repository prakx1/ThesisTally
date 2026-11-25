import json
from contextlib import nullcontext

import httpx
import pytest

from thesistally import kite_session, market


def test_credentials_follow_rotated_file_and_keep_explicit_env_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    path = tmp_path / "session.json"
    monkeypatch.setenv("RADAR_CREDENTIALS", str(path))
    first = {"api_key": "app", "access_token": "first", "source": "handoff"}
    path.write_text(json.dumps({"kite": first}))
    assert market.credentials() == first
    second = {"api_key": "app", "access_token": "second"}
    path.write_text(json.dumps(second))
    assert market.credentials() == second
    monkeypatch.setenv("KITE_API_KEY", "selected-app")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "explicit")
    assert market.credentials() == {"api_key": "selected-app", "access_token": "explicit"}


def test_missing_session_is_distinct_from_server_rejection(tmp_path, monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("RADAR_CREDENTIALS", str(tmp_path / "absent.json"))
    assert kite_session.check_status() == "Session not configured"


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (
            httpx.Response(200, json={"status": "success", "data": {"user_id": "example"}}),
            "Connected",
        ),
        (httpx.Response(401), "Login required"),
        (httpx.Response(403, json={"error_type": "TokenException"}), "Login required"),
        (httpx.Response(403, json={"error_type": "PermissionException"}), "Status unavailable"),
        (httpx.Response(503), "Status unavailable"),
        (httpx.Response(200, json={"status": "success"}), "Status unavailable"),
        (httpx.Response(200, text="bad response"), "Status unavailable"),
        (httpx.ConnectError("offline"), "Status unavailable"),
    ],
)
def test_status_uses_the_profile_and_suppresses_private_response_data(
    monkeypatch, response, status
):
    def respond(request):
        assert request.method == "GET"
        assert str(request.url) == market.KITE + "/user/profile"
        assert request.headers["Authorization"] == "token sample:opaque"
        if isinstance(response, Exception):
            raise response
        return response

    client_type = httpx.Client
    monkeypatch.setattr(
        market.httpx,
        "Client",
        lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw),
    )
    monkeypatch.setenv("KITE_API_KEY", "sample")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "opaque")
    assert kite_session.check_status() == status


def test_login_opens_default_local_helper_only_if_listening(monkeypatch):
    monkeypatch.delenv("RADAR_KITE_LOGIN_URL", raising=False)
    opened = []

    def offline(address, timeout):
        assert address == ("127.0.0.1", 8000)
        raise ConnectionRefusedError()

    def open_browser(url):
        opened.append(url)
        return True

    monkeypatch.setattr(kite_session.socket, "create_connection", offline)
    monkeypatch.setattr(kite_session.webbrowser, "open_new_tab", open_browser)
    with pytest.raises(market.MarketError, match="Start your local Kite login helper"):
        kite_session.open_login()
    assert opened == []
    monkeypatch.setattr(kite_session.socket, "create_connection", lambda *a, **kw: nullcontext())
    assert "browser" in kite_session.open_login()
    assert opened == ["http://127.0.0.1:8000/login"]


@pytest.mark.parametrize("url", ["file:///login", "http://example.org", "https://user@example.org"])
def test_login_rejects_unsafe_urls_before_opening_browser(monkeypatch, url):
    monkeypatch.setenv("RADAR_KITE_LOGIN_URL", url)
    monkeypatch.setattr(kite_session.webbrowser, "open_new_tab", lambda _: pytest.fail())
    with pytest.raises(market.MarketError, match="HTTPS login helper"):
        kite_session.open_login()


def test_login_allows_an_operator_configured_https_helper_and_handles_headless_browser(monkeypatch):
    url = "https://example.org/login"
    monkeypatch.setenv("RADAR_KITE_LOGIN_URL", url)
    monkeypatch.setattr(kite_session.webbrowser, "open_new_tab", lambda target: target == url)
    assert "browser" in kite_session.open_login()
    monkeypatch.setattr(kite_session.webbrowser, "open_new_tab", lambda _: False)
    with pytest.raises(market.MarketError, match="Open the helper configured"):
        kite_session.open_login()


def test_session_and_login_cli(monkeypatch):
    from typer.testing import CliRunner

    from thesistally.cli import app

    runner = CliRunner()
    monkeypatch.setattr(kite_session, "check_status", lambda: kite_session.CONNECTED)
    result = runner.invoke(app, ["session"])
    assert result.exit_code == 0
    assert result.output.strip() == "Kite session accepted."

    monkeypatch.setattr(kite_session, "check_status", lambda: "Status unavailable")
    result = runner.invoke(app, ["session"])
    assert result.exit_code == 1
    assert "Status unavailable" in result.output

    url = "https://example.org/login"
    monkeypatch.setenv("RADAR_KITE_LOGIN_URL", url)
    monkeypatch.setattr(kite_session.webbrowser, "open_new_tab", lambda target: target == url)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 0
    assert "signing in with Zerodha in your browser" in result.output
