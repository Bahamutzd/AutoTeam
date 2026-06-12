import threading

import pytest

from autoteam import api, chatgpt_api


def test_launch_browser_stops_playwright_when_browser_launch_fails(tmp_path, monkeypatch):
    class FakePlaywright:
        def __init__(self):
            self.stopped = False
            self.chromium = self

        def launch(self, **_kwargs):
            raise RuntimeError("proxy launch failed")

        def stop(self):
            self.stopped = True

    class FakeSyncPlaywright:
        def __init__(self, playwright):
            self._playwright = playwright

        def start(self):
            return self._playwright

    fake_playwright = FakePlaywright()
    monkeypatch.setattr(chatgpt_api, "SCREENSHOT_DIR", tmp_path)
    monkeypatch.setattr(chatgpt_api, "get_playwright_launch_options", lambda: {"proxy": {"server": "http://proxy"}})
    monkeypatch.setattr(chatgpt_api, "sync_playwright", lambda: FakeSyncPlaywright(fake_playwright))

    client = chatgpt_api.ChatGPTTeamAPI()

    with pytest.raises(RuntimeError, match="proxy launch failed"):
        client._launch_browser()

    assert fake_playwright.stopped is True
    assert client.playwright is None
    assert client.browser is None
    assert client.context is None
    assert client.page is None


def test_post_admin_login_start_stops_api_when_begin_login_fails(monkeypatch):
    instances = []

    class FakeChatGPTTeamAPI:
        def __init__(self):
            self.stopped = False
            instances.append(self)

        def begin_admin_login(self, _email):
            raise RuntimeError("proxy launch failed")

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(api, "_playwright_lock", threading.Lock())
    monkeypatch.setattr(api, "_admin_login_api", None)
    monkeypatch.setattr(api, "_admin_login_step", None)
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **_kwargs: func(*args))
    monkeypatch.setattr("autoteam.chatgpt_api.ChatGPTTeamAPI", FakeChatGPTTeamAPI)

    with pytest.raises(api.HTTPException) as exc:
        api.post_admin_login_start(api.AdminEmailParams(email="admin@example.com"))

    assert exc.value.status_code == 400
    assert "proxy launch failed" in str(exc.value.detail)
    assert len(instances) == 1
    assert instances[0].stopped is True
    assert api._admin_login_api is None
    assert api._playwright_lock.locked() is False


def test_post_admin_login_start_marks_pending_while_starting(monkeypatch):
    class FakeChatGPTTeamAPI:
        def __init__(self):
            self.login_email = None

        def begin_admin_login(self, email):
            assert api._admin_login_api is self
            assert api._admin_login_step == "starting"
            assert self.login_email == email
            return {"step": "email_required", "detail": "manual takeover"}

        def stop(self):
            pass

    def fake_run(func, *args, **kwargs):
        assert kwargs.get("timeout_seconds") == api.ADMIN_LOGIN_START_TIMEOUT_SECONDS
        return func(*args)

    monkeypatch.setattr(api, "_playwright_lock", threading.Lock())
    monkeypatch.setattr(api, "_admin_login_api", None)
    monkeypatch.setattr(api, "_admin_login_step", None)
    monkeypatch.setattr(api._pw_executor, "run", fake_run)
    monkeypatch.setattr("autoteam.chatgpt_api.ChatGPTTeamAPI", FakeChatGPTTeamAPI)

    result = api.post_admin_login_start(api.AdminEmailParams(email="admin@example.com"))

    assert result["status"] == "email_required"
    assert result["admin"]["login_in_progress"] is True
    assert result["admin"]["login_step"] == "email_required"
    assert result["admin"]["login_email"] == "admin@example.com"
    assert api._playwright_lock.locked() is True


def test_get_team_members_stops_chatgpt_when_start_fails(monkeypatch):
    instances = []

    class FakeChatGPTTeamAPI:
        def __init__(self):
            self.stopped = False
            instances.append(self)

        def start(self):
            raise RuntimeError("http proxy failed")

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(api, "_playwright_lock", threading.Lock())
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args, **kwargs))
    monkeypatch.setattr("autoteam.admin_state.get_admin_session_token", lambda: "session")
    monkeypatch.setattr("autoteam.admin_state.get_chatgpt_account_id", lambda: "acc-1")
    monkeypatch.setattr("autoteam.chatgpt_api.ChatGPTTeamAPI", FakeChatGPTTeamAPI)

    with pytest.raises(api.HTTPException) as exc:
        api.get_team_members()

    assert exc.value.status_code == 502
    assert "http proxy failed" in str(exc.value.detail)
    assert len(instances) == 1
    assert instances[0].stopped is True
    assert api._playwright_lock.locked() is False
