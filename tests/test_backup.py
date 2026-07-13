import pytest
import os
from stocks_power_rich.offsite_backup import push_offsite, mask_secrets

def test_push_offsite_skipped_when_no_env(monkeypatch):
    monkeypatch.setenv("SPR_BACKUP_GIT_REMOTE", "")
    res = push_offsite("dummy_path")
    assert res == {"ok": False, "skipped": True}

def test_mask_secrets():
    assert mask_secrets("https://token@github.com/foo.git", "https://token@github.com/foo.git") == "https://***@github.com/foo.git"
    assert mask_secrets("https://oauth2:token@github.com/foo.git", "https://oauth2:token@github.com/foo.git") == "https://***@github.com/foo.git"
    assert mask_secrets("some normal output", "") == "some normal output"

def test_push_offsite_executes_git(tmp_path, monkeypatch):
    backup_file = tmp_path / "spr-20260713.sqlite"
    backup_file.write_text("dummy sqlite database content")

    monkeypatch.setenv("SPR_BACKUP_GIT_REMOTE", "https://my-secret-token@github.com/test/repo.git")
    monkeypatch.setenv("SPR_BACKUP_GIT_BRANCH", "test-backup-branch")

    called_commands = []
    def mock_subprocess_run(args, cwd=None, capture_output=False, text=False, check=False):
        called_commands.append((args, cwd))
        class DummyCompletedProcess:
            returncode = 0
            stdout = "success"
            stderr = ""
        return DummyCompletedProcess()

    import subprocess
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    res = push_offsite(str(backup_file))
    assert res["ok"] is True
    assert res["file"] == "spr-20260713.sqlite"

    clone_cmd, _ = called_commands[0]
    assert "git" in clone_cmd
    assert "clone" in clone_cmd
    assert "test-backup-branch" in clone_cmd
    assert "https://my-secret-token@github.com/test/repo.git" in clone_cmd
