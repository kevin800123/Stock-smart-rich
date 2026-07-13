import os
import shutil
import subprocess
import re
from .config import load_config

def mask_secrets(text: str, remote: str) -> str:
    if not text:
        return text
    # Mask git urls with credentials, e.g. https://token@github.com/ or https://user:pass@github.com/
    masked = text
    masked = re.sub(r'https?://[^@\s]+@', 'https://***@', masked)
    return masked

def run_git(args, cwd, remote):
    res = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    return res.returncode, mask_secrets(res.stdout, remote), mask_secrets(res.stderr, remote)

def push_offsite(local_path: str) -> dict:
    """把最新備份送到外部（Git 方案或未來 S3 等）。
    不拋出例外，出錯時回傳 {"ok": False, "error": str}，未配置時回傳 {"ok": False, "skipped": True}。
    """
    cfg = load_config()
    remote = cfg.backup_git_remote
    branch = cfg.backup_git_branch or "backup"
    if not remote:
        return {"ok": False, "skipped": True}

    if not os.path.exists(local_path):
        return {"ok": False, "error": f"Backup file {local_path} not found"}

    # Define temp dir inside workspace
    workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    temp_dir = os.path.join(workspace_dir, "data", "git_backup_temp")

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # Try clone branch
        code, out, err = run_git(["git", "clone", "--depth", "1", "-b", branch, remote, "."], cwd=temp_dir, remote=remote)
        cloned = (code == 0)

        if not cloned:
            # Try clone default branch
            shutil.rmtree(temp_dir, ignore_errors=True)
            os.makedirs(temp_dir, exist_ok=True)
            code, out, err = run_git(["git", "clone", "--depth", "1", remote, "."], cwd=temp_dir, remote=remote)
            if code == 0:
                # Checkout new branch
                run_git(["git", "checkout", "-b", branch], cwd=temp_dir, remote=remote)
                cloned = True
            else:
                # Empty repo or connection error. Try git init
                shutil.rmtree(temp_dir, ignore_errors=True)
                os.makedirs(temp_dir, exist_ok=True)
                run_git(["git", "init"], cwd=temp_dir, remote=remote)
                run_git(["git", "remote", "add", "origin", remote], cwd=temp_dir, remote=remote)
                run_git(["git", "checkout", "-b", branch], cwd=temp_dir, remote=remote)
                cloned = True

        # Configure user
        run_git(["git", "config", "user.name", "SPR Backup Bot"], cwd=temp_dir, remote=remote)
        run_git(["git", "config", "user.email", "backup-bot@stocks-power-rich.local"], cwd=temp_dir, remote=remote)

        # Copy file
        filename = os.path.basename(local_path)
        dest_path = os.path.join(temp_dir, filename)
        shutil.copy2(local_path, dest_path)

        # Commit and push
        run_git(["git", "add", filename], cwd=temp_dir, remote=remote)
        code, out, err = run_git(["git", "commit", "-m", f"Backup {filename}"], cwd=temp_dir, remote=remote)
        if "nothing to commit" in out or "nothing to commit" in err:
            return {"ok": True, "info": "Nothing to commit"}

        code, out, err = run_git(["git", "push", "-u", "origin", branch], cwd=temp_dir, remote=remote)
        if code != 0:
            # Try push force
            code, out, err = run_git(["git", "push", "-f", "origin", branch], cwd=temp_dir, remote=remote)
            if code != 0:
                raise Exception(f"Git push failed: {err}")

        return {"ok": True, "file": filename}
    except Exception as e:
        return {"ok": False, "error": mask_secrets(str(e), remote)}
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
