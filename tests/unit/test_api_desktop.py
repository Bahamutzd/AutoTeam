from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request

from autoteam import api


def _request(token: str = "") -> Request:
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/desktop/status",
            "headers": headers,
            "query_string": b"",
        }
    )


def test_desktop_url_uses_encoded_path_token(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "api-key/with-symbols")

    url = api._desktop_url_for_request(_request("api-key/with-symbols"))
    path = parse_qs(urlsplit(url).query)["path"][0]
    encoded_token = path.rsplit("/", 1)[-1]

    assert path.startswith("api/desktop/ws/")
    assert "key=" not in path
    assert api._decode_desktop_token(encoded_token) == "api-key/with-symbols"


def test_desktop_status_reports_missing_novnc_page(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "")
    monkeypatch.setattr(api, "_vnc_ready", lambda _host, _port: True)
    monkeypatch.setenv("ENABLE_NOVNC", "true")
    monkeypatch.setenv("NOVNC_WEB_DIR", str(tmp_path))

    status = api.get_desktop_status(_request())

    assert status["enabled"] is False
    assert status["web_available"] is False
    assert "未找到 noVNC 页面目录" in status["detail"]


def test_desktop_status_returns_embedded_url_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "secret")
    monkeypatch.setattr(api, "_vnc_ready", lambda _host, _port: True)
    monkeypatch.setenv("ENABLE_NOVNC", "true")
    monkeypatch.setenv("NOVNC_WEB_DIR", str(tmp_path))
    (tmp_path / "vnc.html").write_text("", encoding="utf-8")

    status = api.get_desktop_status(_request("secret"))

    assert status["enabled"] is True
    assert status["web_available"] is True
    assert status["vnc_ready"] is True
    assert status["url"].startswith("/desktop/vnc.html?")
    assert parse_qs(urlsplit(status["url"]).query)["path"][0].startswith("api/desktop/ws/")
