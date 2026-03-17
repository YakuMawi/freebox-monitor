"""
updater.py — Vérification et application des mises à jour GitHub.
"""
import os
import io
import shutil
import zipfile
import logging
import tempfile

import requests

log = logging.getLogger(__name__)

VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")

EXCLUDE = {"data/", "certs/", "credentials.json", "venv/", "__pycache__/", ".claude/"}


def get_current_version() -> str:
    try:
        with open(VERSION_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "0.0.0"


def check_for_update(repo: str, token: str = None) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"available": False, "error": str(e)}

    latest = data.get("tag_name", "").lstrip("v")
    current = get_current_version()

    return {
        "available": latest != current and latest > current,
        "current": current,
        "latest": latest,
        "changelog": data.get("body", ""),
        "download_url": data.get("zipball_url", ""),
    }


def apply_update(repo: str, token: str = None) -> tuple:
    info = check_for_update(repo, token)
    if not info.get("available"):
        return False, "Aucune mise à jour disponible"

    download_url = info.get("download_url")
    if not download_url:
        return False, "URL de téléchargement introuvable"

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        r = requests.get(download_url, headers=headers, timeout=60, stream=True)
        r.raise_for_status()

        project_dir = os.path.dirname(os.path.abspath(__file__))

        # Create backup
        backup_dir = os.path.join(project_dir, "data", "backup_before_update")
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        os.makedirs(backup_dir, exist_ok=True)

        # Save current files for rollback
        for item in os.listdir(project_dir):
            if any(item.rstrip("/") == ex.rstrip("/") for ex in EXCLUDE):
                continue
            src = os.path.join(project_dir, item)
            dst = os.path.join(backup_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        # Extract update to temp dir
        with tempfile.TemporaryDirectory() as tmp:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            z.extractall(tmp)

            # GitHub zipball has a top-level directory
            entries = os.listdir(tmp)
            if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
                src_dir = os.path.join(tmp, entries[0])
            else:
                src_dir = tmp

            # Copy files, excluding protected paths
            for item in os.listdir(src_dir):
                if any(item.rstrip("/") == ex.rstrip("/") for ex in EXCLUDE):
                    continue
                src = os.path.join(src_dir, item)
                dst = os.path.join(project_dir, item)
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

        return True, f"Mise à jour vers {info['latest']} effectuée"

    except Exception as e:
        log.error("Erreur mise à jour: %s", e)
        return False, str(e)
