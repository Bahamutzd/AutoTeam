from autoteam import chatgpt_api


def test_workspace_candidate_kind_filters_page_heading_and_legal_links():
    assert chatgpt_api._workspace_candidate_kind("Choose a workspace") is None
    assert chatgpt_api._workspace_candidate_kind("Terms of Use") is None
    assert chatgpt_api._workspace_candidate_kind("Privacy Policy") is None


def test_workspace_candidate_kind_keeps_real_workspace_and_marks_personal_fallback():
    assert chatgpt_api._workspace_candidate_kind("Idapro") == "preferred"
    assert chatgpt_api._workspace_candidate_kind("Personal account") == "fallback"


def test_wait_for_post_workspace_ready_accepts_chatgpt_page_after_body_appears(monkeypatch):
    class FakePage:
        url = "https://chatgpt.com/"

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    states = iter(["", "", "Chat history"])
    monkeypatch.setattr(client, "_extract_session_token", lambda: "")
    monkeypatch.setattr(client, "_body_excerpt", lambda limit=120: next(states))
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)

    assert client._wait_for_post_workspace_ready(timeout=2) is True


def test_wait_for_post_workspace_ready_accepts_blank_chatgpt_page_after_retries(monkeypatch):
    class FakePage:
        url = "https://chatgpt.com/"

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    monkeypatch.setattr(client, "_extract_session_token", lambda: "")
    monkeypatch.setattr(client, "_body_excerpt", lambda limit=120: "")
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)

    assert client._wait_for_post_workspace_ready(timeout=2) is True


def test_select_workspace_option_shortcuts_completed_when_chatgpt_home_loaded(monkeypatch):
    class FakePage:
        url = "https://chatgpt.com/"

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    monkeypatch.setattr(client, "_list_workspace_options", lambda: [{"id": "0", "label": "Idapro"}])
    monkeypatch.setattr(client, "_click_workspace_option_by_label", lambda label: True)
    monkeypatch.setattr(client, "_wait_for_workspace_selection_exit", lambda timeout=15: True)
    monkeypatch.setattr(client, "_wait_for_post_workspace_ready", lambda timeout=12: True)
    monkeypatch.setattr(client, "_log_login_state", lambda label: None)
    monkeypatch.setattr(
        client,
        "_detect_login_step",
        lambda: (_ for _ in ()).throw(AssertionError("should not reach _detect_login_step")),
    )

    assert client.select_workspace_option(0) == {"step": "completed", "detail": None}


def test_login_page_url_detection_accepts_openai_auth_and_chatgpt():
    assert chatgpt_api._is_login_page_url("https://auth.openai.com/log-in")
    assert chatgpt_api._is_login_page_url("https://auth.openai.com/u/login/identifier")
    assert chatgpt_api._is_login_page_url("https://chatgpt.com/auth/login")


def test_visible_email_locator_uses_typeable_chinese_label(monkeypatch):
    class FakeLocator:
        @property
        def first(self):
            return self

        def is_visible(self, timeout=None):
            return True

    class FakeFrame:
        def __init__(self):
            self.locator_value = FakeLocator()
            self.seen_label_texts = None

        def evaluate(self, _script, payload):
            self.seen_label_texts = payload[0]
            return {"found": True, "reason": "label-focus", "tag": "INPUT"}

        def locator(self, selector):
            assert "data-autoteam-auth-email" in selector
            return self.locator_value

    class FakePage:
        def __init__(self):
            self.main_frame = FakeFrame()
            self.frames = [self.main_frame]

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    monkeypatch.setattr(client, "_visible_locator_in_frames", lambda _selectors, timeout_ms=400: None)

    assert client._visible_email_locator(timeout_ms=100) is client.page.main_frame.locator_value
    assert "电子邮件地址" in client.page.main_frame.seen_label_texts


def test_open_login_page_uses_openai_auth_when_email_input_visible(monkeypatch):
    class FakeLoginButton:
        @property
        def first(self):
            return self

        def is_visible(self, timeout=None):
            return False

    class FakePage:
        def __init__(self):
            self.url = ""
            self.visited = []

        def goto(self, url, *_args, **_kwargs):
            self.url = url
            self.visited.append(url)

        def locator(self, *_args, **_kwargs):
            return FakeLoginButton()

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()

    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_wait_for_cloudflare", lambda: None)
    monkeypatch.setattr(client, "_log_login_state", lambda _label: None)
    monkeypatch.setattr(client, "_wait_for_login_step", lambda _steps, timeout=4: ("email_required", None))
    monkeypatch.setattr(client, "_visible_email_locator", lambda timeout_ms=1500: object())

    selected = client._open_login_page()

    assert selected == "https://auth.openai.com/log-in"
    assert client.page.visited == ["https://auth.openai.com/log-in"]


def test_open_login_page_falls_back_to_chatgpt_login_when_auth_email_missing(monkeypatch):
    class FakeLoginButton:
        @property
        def first(self):
            return self

        def is_visible(self, timeout=None):
            return False

    class FakePage:
        def __init__(self):
            self.url = ""
            self.visited = []

        def goto(self, url, *_args, **_kwargs):
            self.url = url
            self.visited.append(url)

        def locator(self, *_args, **_kwargs):
            return FakeLoginButton()

    client = chatgpt_api.ChatGPTTeamAPI()
    client.page = FakePage()
    email_inputs = iter([None, object()])

    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_wait_for_cloudflare", lambda: None)
    monkeypatch.setattr(client, "_log_login_state", lambda _label: None)
    monkeypatch.setattr(client, "_wait_for_login_step", lambda _steps, timeout=4: ("email_required", None))
    monkeypatch.setattr(client, "_visible_email_locator", lambda timeout_ms=1500: next(email_inputs))

    selected = client._open_login_page()

    assert selected == "https://chatgpt.com/auth/login"
    assert client.page.visited == ["https://auth.openai.com/log-in", "https://chatgpt.com/auth/login"]


def test_begin_login_keeps_auth_login_page_for_manual_takeover(monkeypatch, tmp_path):
    class FakeLocator:
        def inner_text(self, timeout=None):
            return ""

    class FakePage:
        url = "https://chatgpt.com/auth/login"

        def goto(self, *_args, **_kwargs):
            return None

        def locator(self, *_args, **_kwargs):
            return FakeLocator()

        def screenshot(self, path=None, full_page=None):
            return None

    client = chatgpt_api.ChatGPTTeamAPI()
    client.browser = object()
    client.page = FakePage()

    monkeypatch.setattr(chatgpt_api, "SCREENSHOT_DIR", tmp_path)
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(client, "_wait_for_cloudflare", lambda: None)
    monkeypatch.setattr(client, "_log_login_state", lambda _label: None)
    monkeypatch.setattr(client, "_open_login_page", lambda: None)
    monkeypatch.setattr(client, "_wait_for_login_step", lambda _steps, timeout=12: ("email_required", None))
    monkeypatch.setattr(client, "_visible_email_locator", lambda timeout_ms=5000: None)

    result = client.begin_login("admin@example.com", actor_label="管理员")

    assert result["step"] == "email_required"
    assert "未找到可见邮箱输入框" in result["detail"]
    assert "手动接管" in result["detail"]
