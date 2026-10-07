"""
updater.py — Vérification et application des mises à jour GitHub.
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
import logging
import tempfile

import requests

_REPO_RE = re.compile(r'^[a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+$')
_TAG_RE  = re.compile(r'^[a-zA-Z0-9_.\-]+$')

log = logging.getLogger(__name__)

VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")

EXCLUDE = {"data/", "certs/", "credentials.json", "venv/", "__pycache__/", ".claude/", ".git/"}


def parse_version(v: str) -> tuple:
    """Convertit '1.10.0' (ou 'v1.10.0-rc1') en tuple d'entiers comparable.

    Indispensable : la comparaison lexicographique de chaînes donnait
    '1.10.0' < '1.9.0' (car '1' < '9' au 3ᵉ caractère), donc une mise à jour
    mineure au-delà de .9 n'était jamais proposée. Les segments non numériques
    (suffixes de pré-version) sont ignorés.
    """
    nums = re.findall(r'\d+', v or "")
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def is_newer(latest: str, current: str) -> bool:
    """True si `latest` est strictement postérieure à `current`."""
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


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
        "available": is_newer(latest, current),
        "current": current,
        "latest": latest,
        "tag_name": data.get("tag_name", ""),
        "changelog": data.get("body", ""),
        "download_url": data.get("zipball_url", ""),
    }


def list_releases(repo: str, token: str = None) -> list:
    url = f"https://api.github.com/repos/{repo}/releases"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        releases = r.json()
        if not releases:
            return {"error": "Aucune release trouvée sur ce dépôt"}
        return [
            {
                "tag":  rel.get("tag_name", ""),
                "name": rel.get("name", ""),
                "date": rel.get("published_at", "")[:10],
            }
            for rel in releases
        ]
    except Exception as e:
        return {"error": str(e)}


def apply_update(repo: str, token: str = None, tag: str = None) -> tuple:
    if not _REPO_RE.match(repo):
        return False, "Dépôt GitHub invalide"
    if tag and not _TAG_RE.match(tag):
        return False, "Tag de version invalide"
    if tag:
        # Version spécifique (mise à jour ou rétrogradation)
        url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            download_url = data.get("zipball_url", "")
            target_version = tag.lstrip("v")
        except Exception as e:
            return False, f"Release introuvable : {e}"
    else:
        info = check_for_update(repo, token)
        if not info.get("available"):
            return False, "Aucune mise à jour disponible"
        download_url = info.get("download_url")
        target_version = info.get("latest", "")

    if not download_url:
        return False, "URL de téléchargement introuvable"

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    project_dir = os.path.dirname(os.path.abspath(__file__))
    backup_dir  = os.path.join(project_dir, "data", "backup_before_update")
    applied_changes = False  # True dès que project_dir commence à être modifié

    tmp_zip_fd, tmp_zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(tmp_zip_fd)
    try:
        # Téléchargement en flux, écrit sur disque par chunks : évite de charger
        # l'archive entière en RAM via r.content pour les gros dépôts.
        r = requests.get(download_url, headers=headers, timeout=60, stream=True)
        r.raise_for_status()
        with open(tmp_zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

        # Create backup (avant toute modification de project_dir)
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
            with zipfile.ZipFile(tmp_zip_path) as z:
                z.extractall(tmp)

            # GitHub zipball has a top-level directory
            entries = os.listdir(tmp)
            if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
                src_dir = os.path.join(tmp, entries[0])
            else:
                src_dir = tmp

            # Copy files, excluding protected paths — à partir d'ici, project_dir
            # est modifié : un échec doit restaurer la sauvegarde.
            applied_changes = True
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

        # Mise à jour des dépendances Python (échec non bloquant : pas de rollback)
        pip = os.path.join(project_dir, "venv", "bin", "pip")
        req = os.path.join(project_dir, "requirements.txt")
        if os.path.exists(pip) and os.path.exists(req):
            result = subprocess.run(
                [pip, "install", "-q", "-r", req],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                log.warning("pip install partiel : %s", result.stderr)
        else:
            # Fallback : pip du venv courant (sys.executable)
            pip_fallback = os.path.join(os.path.dirname(sys.executable), "pip")
            if os.path.exists(pip_fallback) and os.path.exists(req):
                subprocess.run([pip_fallback, "install", "-q", "-r", req],
                               capture_output=True, timeout=120)

        return True, f"Version {target_version} installée"

    except Exception as e:
        log.error("Erreur mise à jour: %s", e)
        if applied_changes:
            if _restore_backup(project_dir, backup_dir):
                log.warning("Mise à jour échouée — restauration de la sauvegarde réussie")
                return False, f"Mise à jour échouée (version précédente restaurée) : {e}"
            log.error("Mise à jour échouée ET restauration de la sauvegarde impossible")
            return False, f"Mise à jour échouée ET restauration impossible — intervention manuelle requise : {e}"
        return False, str(e)
    finally:
        try:
            os.unlink(tmp_zip_path)
        except OSError:
            pass


def _restore_backup(project_dir: str, backup_dir: str) -> bool:
    """Restaure project_dir depuis backup_dir après un échec de mise à jour.
    Retourne True si la restauration a réussi, False sinon."""
    if not os.path.isdir(backup_dir):
        log.error("Aucune sauvegarde disponible dans %s — restauration impossible", backup_dir)
        return False
    try:
        for item in os.listdir(backup_dir):
            src = os.path.join(backup_dir, item)
            dst = os.path.join(project_dir, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        return True
    except Exception as e:
        log.error("Échec de la restauration post-échec de mise à jour: %s", e)
        return False
