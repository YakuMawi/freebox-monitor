"""
crypto.py — Chiffrement des secrets au repos (Fernet).
La clé maître est cherchée dans cet ordre :
  1. Variable d'environnement FBX_MASTER_KEY (base64url 32 bytes)
  2. Fichier data/.secret_key (chmod 600)
  3. Génération automatique au premier démarrage → stocké dans data/.secret_key
"""
import os
import logging
from cryptography.fernet import Fernet

log = logging.getLogger(__name__)

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", ".secret_key")
_FERNET = None


def _load_or_create_key() -> Fernet:
    global _FERNET
    if _FERNET:
        return _FERNET
    # 1. Variable d'environnement
    env_key = os.environ.get("FBX_MASTER_KEY", "").strip()
    if env_key:
        _FERNET = Fernet(env_key.encode())
        return _FERNET
    # 2. Fichier keyfile
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            raw = f.read().strip()
        _FERNET = Fernet(raw)
        return _FERNET
    # 3. Génération automatique
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    os.chmod(KEY_FILE, 0o600)
    _FERNET = Fernet(key)
    return _FERNET


PREFIX = "enc:"


def encrypt(plaintext: str) -> str:
    """Chiffre une chaîne → retourne 'enc:<token_base64>'."""
    if not plaintext:
        return plaintext
    f = _load_or_create_key()
    token = f.encrypt(plaintext.encode()).decode()
    return PREFIX + token


def decrypt(value: str) -> str:
    """Déchiffre une valeur 'enc:<token>'. Retourne la valeur telle quelle si non chiffrée."""
    if not value or not value.startswith(PREFIX):
        return value  # valeur plaintext ou vide → retour direct (migration)
    f = _load_or_create_key()
    try:
        return f.decrypt(value[len(PREFIX):].encode()).decode()
    except Exception as e:
        # Token corrompu ou clé maître différente (data/.secret_key régénéré,
        # FBX_MASTER_KEY changée…). Renvoyer "" en silence rendait l'envoi
        # d'emails SMTP muet et indébogable : on trace sans jamais logger le
        # token ni la valeur déchiffrée.
        log.error(
            "Échec de déchiffrement d'un secret (%s) — clé maître différente ou "
            "valeur corrompue ; la valeur est traitée comme vide. "
            "Ressaisissez le secret concerné dans les réglages.",
            type(e).__name__
        )
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(PREFIX)
