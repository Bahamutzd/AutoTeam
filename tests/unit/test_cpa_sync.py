import base64
import json
from pathlib import Path

from autoteam import cpa_sync


def _auth_content_with_plan(plan):
    payload = {"https://api.openai.com/auth": {"chatgpt_plan_type": plan}}
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return json.dumps({"type": "codex", "id_token": f"header.{encoded_payload}.signature"})


def test_infer_plan_from_name_handles_unhashed_and_windows_copy_names():
    cases = {
        "codex-user@example.com-team-dff1f608.json": "team",
        "codex-user@example.com-plus.json": "plus",
        "codex-user@example.com-plus (1).json": "plus",
        "codex-user@example.com-pro.json": "pro",
        "codex-user@example.com-pro (1).json": "pro",
        "codex-user@example.com-prolite.json": "prolite",
    }

    for name, expected in cases.items():
        assert cpa_sync._infer_plan_from_name(name) == expected


def test_resolve_cpa_plan_type_prefers_metadata_before_download(monkeypatch):
    def fail_download(_name):
        raise AssertionError("metadata plan should not download auth content")

    monkeypatch.setattr(cpa_sync, "download_from_cpa", fail_download)

    assert cpa_sync._resolve_cpa_plan_type("codex-user@example.com-free.json", {"plan_type": "plus"}) == "plus"


def test_sync_to_cpa_skips_disabled_accounts_and_deletes_remote_copy(monkeypatch, tmp_path):
    enabled_auth = tmp_path / "codex-enabled@example.com-team-a.json"
    disabled_auth = tmp_path / "codex-disabled@example.com-team-b.json"
    enabled_auth.write_text("{}", encoding="utf-8")
    disabled_auth.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": "enabled@example.com", "status": "active", "auth_file": str(enabled_auth), "disabled": False},
            {"email": "disabled@example.com", "status": "active", "auth_file": str(disabled_auth), "disabled": True},
        ],
    )
    monkeypatch.setattr("autoteam.accounts.save_accounts", lambda _accounts: None)
    monkeypatch.setattr(cpa_sync, "_cleanup_local_duplicates", lambda _accounts: (0, False))
    monkeypatch.setattr("autoteam.config.SYNC_KEEP_PLANS", "")
    monkeypatch.setattr(
        cpa_sync,
        "list_cpa_files",
        lambda: [
            {"name": enabled_auth.name, "email": "enabled@example.com"},
            {"name": disabled_auth.name, "email": "disabled@example.com"},
        ],
    )

    uploaded = []
    deleted = []
    monkeypatch.setattr(cpa_sync, "upload_to_cpa", lambda path: uploaded.append(Path(path).name) or True)
    monkeypatch.setattr(cpa_sync, "patch_cpa_priority", lambda _name, _priority: True)
    monkeypatch.setattr(cpa_sync, "delete_from_cpa", lambda name: deleted.append(name) or True)

    cpa_sync.sync_to_cpa()

    assert uploaded == [enabled_auth.name]
    assert deleted == [disabled_auth.name]


def test_sync_to_cpa_uses_downloaded_plan_before_filename(monkeypatch):
    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": "plus@example.com", "status": "standby", "auth_file": "", "disabled": False},
        ],
    )
    monkeypatch.setattr("autoteam.accounts.save_accounts", lambda _accounts: None)
    monkeypatch.setattr(cpa_sync, "_cleanup_local_duplicates", lambda _accounts: (0, False))
    monkeypatch.setattr("autoteam.config.SYNC_KEEP_PLANS", "plus")
    monkeypatch.setattr(
        cpa_sync,
        "list_cpa_files",
        lambda: [
            {"name": "codex-plus@example.com-plus.json", "email": "plus@example.com"},
        ],
    )
    monkeypatch.setattr(cpa_sync, "download_from_cpa", lambda _name: _auth_content_with_plan("free"))

    deleted = []
    monkeypatch.setattr(cpa_sync, "delete_from_cpa", lambda name: deleted.append(name) or True)

    cpa_sync.sync_to_cpa()

    assert deleted == ["codex-plus@example.com-plus.json"]


def test_sync_main_codex_to_cpa_sets_lowest_priority(monkeypatch, tmp_path):
    main_auth = tmp_path / "codex-main-d696cc72.json"
    main_auth.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cpa_sync,
        "list_cpa_files",
        lambda: [
            {"name": "codex-main-old.json", "email": ""},
        ],
    )

    deleted = []
    patched = []
    monkeypatch.setattr(cpa_sync, "delete_from_cpa", lambda name: deleted.append(name) or True)
    monkeypatch.setattr(cpa_sync, "upload_to_cpa", lambda path: Path(path).name == main_auth.name)
    monkeypatch.setattr(cpa_sync, "patch_cpa_priority", lambda name, priority: patched.append((name, priority)) or True)

    result = cpa_sync.sync_main_codex_to_cpa(main_auth)

    assert deleted == ["codex-main-old.json"]
    assert patched == [(main_auth.name, 1)]
    assert result == {"uploaded": main_auth.name}


def test_sync_to_cpa_respects_keep_plans_for_unhashed_cpa_files(monkeypatch, tmp_path):
    active_auth = tmp_path / "codex-active@example.com-team-a.json"
    active_auth.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        lambda: [
            {"email": "active@example.com", "status": "active", "auth_file": str(active_auth), "disabled": False},
            {"email": "plus@example.com", "status": "standby", "auth_file": "", "disabled": False},
            {"email": "copy@example.com", "status": "standby", "auth_file": "", "disabled": False},
            {"email": "pro@example.com", "status": "standby", "auth_file": "", "disabled": False},
            {"email": "team@example.com", "status": "standby", "auth_file": "", "disabled": False},
        ],
    )
    monkeypatch.setattr("autoteam.accounts.save_accounts", lambda _accounts: None)
    monkeypatch.setattr(cpa_sync, "_cleanup_local_duplicates", lambda _accounts: (0, False))
    monkeypatch.setattr("autoteam.config.SYNC_KEEP_PLANS", "plus,pro")
    monkeypatch.setattr(
        cpa_sync,
        "list_cpa_files",
        lambda: [
            {"name": active_auth.name, "email": "active@example.com"},
            {"name": "codex-plus@example.com-plus.json", "email": "plus@example.com"},
            {"name": "codex-copy@example.com-plus (1).json", "email": "copy@example.com"},
            {"name": "codex-pro@example.com-pro.json", "email": "pro@example.com"},
            {"name": "codex-team@example.com-team-dff1f608.json", "email": "team@example.com"},
        ],
    )

    uploaded = []
    deleted = []
    monkeypatch.setattr(cpa_sync, "upload_to_cpa", lambda path: uploaded.append(Path(path).name) or True)
    monkeypatch.setattr(cpa_sync, "patch_cpa_priority", lambda _name, _priority: True)
    monkeypatch.setattr(cpa_sync, "download_from_cpa", lambda _name: None)
    monkeypatch.setattr(cpa_sync, "delete_from_cpa", lambda name: deleted.append(name) or True)

    cpa_sync.sync_to_cpa()

    assert uploaded == [active_auth.name]
    assert deleted == ["codex-team@example.com-team-dff1f608.json"]
