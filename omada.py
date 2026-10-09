"""
omada.py — Intégration Omada OpenAPI (TP-Link) pour récupérer les événements WAN
(détection de lien, bascule de secours) du routeur ER8411 géré par un contrôleur
Omada Cloud/SDN, et les stocker dans la table SQLite `router_events`.

Référence des endpoints (confirmés depuis la spécification OpenAPI v3 officielle
exposée par le contrôleur, et depuis des intégrations tierces établies — ioBroker.omada,
ha-omada-open-api) :
  - POST {connector_url}/openapi/authorize/token?grant_type=client_credentials
        body JSON: {omadacId, client_id, client_secret}
        → {errorCode, msg, result:{accessToken, refreshToken, expiresIn, tokenType}}
  - GET  {connector_url}/openapi/v1/{omadacId}/sites?page=&pageSize=
        header Authorization: AccessToken=<token>
        → {errorCode, msg, result:{totalRows, currentPage, currentSize, data:[{siteId, name, ...}]}}
  - GET  {connector_url}/openapi/v1/{omadacId}/sites/{siteId}/logs/alerts
             ?page=&pageSize=&filters.timeStart=<ms>&filters.timeEnd=<ms>
        → {errorCode, msg, result:{totalRows, currentPage, currentSize,
                                    data:[{id, key, module, content, time, level}]}}
    C'est ce flux "alerts" (avec un champ `level`) qui correspond aux lignes que
    l'utilisateur voit dans l'UI Omada ("WAN Online Detection | Info | ...",
    "WAN Link Backup | Critical | ..." ) — le flux "logs/events" existe aussi mais
    son schéma n'a pas de champ `level`.

Isolation : AUCUNE exception ne doit sortir de ce module vers monitor.py. Toute
erreur (credentials absents/invalides, token expiré, service Omada Cloud
indisponible, réponse inattendue) est journalisée et traitée comme une collecte
sans résultat ; elle n'affecte jamais la collecte Freebox principale.
"""
import json
import logging
import threading
import time

import requests

import db

log = logging.getLogger(__name__)

_HTTP_TIMEOUT = 15
_TOKEN_SAFETY_MARGIN_S = 60       # renouvelle le token 60s avant son expiration réelle
_INITIAL_LOOKBACK_MS   = 24 * 3600 * 1000   # 24h au premier démarrage, pour peupler le dashboard
_OVERLAP_MS            = 5 * 60 * 1000      # recoupe 5 min avec le fetch précédent (dédoublonné par event_id)

# Codes d'erreur Omada associés à un token absent/expiré/invalide, qui doivent
# déclencher un nouvel essai avec un token tout neuf (voir ioBroker.omada /
# ha-omada-open-api, qui traitent ces codes de façon identique).
_TOKEN_ERROR_CODES = {-1200, -44112, -44113, -44116}

_lock = threading.Lock()
_access_token  = None
_token_expires_at = 0.0  # time.monotonic()


def _cfg() -> dict:
    return {
        "connector_url": (db.get_config("omada_connector_url", "") or "").rstrip("/"),
        "omadac_id":      db.get_config("omada_omadac_id", "") or "",
        "client_id":      db.get_config("omada_client_id", "") or "",
        "client_secret":  db.get_config("omada_client_secret", "") or "",
        "site_id":        db.get_config("omada_site_id", "") or "",
        "device_id":      db.get_config("omada_device_id", "") or "",
    }


def is_configured(cfg: dict = None) -> bool:
    cfg = cfg or _cfg()
    return bool(cfg["connector_url"] and cfg["omadac_id"]
                and cfg["client_id"] and cfg["client_secret"])


def _invalidate_token():
    global _access_token, _token_expires_at
    _access_token = None
    _token_expires_at = 0.0


def _request_new_token(cfg: dict):
    """Demande un nouvel access token via client_credentials. Retourne le token
    (str) ou None en cas d'échec (jamais d'exception)."""
    url = f"{cfg['connector_url']}/openapi/authorize/token"
    try:
        r = requests.post(
            url,
            params={"grant_type": "client_credentials"},
            json={
                "omadacId":      cfg["omadac_id"],
                "client_id":     cfg["client_id"],
                "client_secret": cfg["client_secret"],
            },
            headers={"Content-Type": "application/json"},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        log.warning("omada: échec réseau lors de l'authentification (%s)", e)
        return None

    try:
        d = r.json()
    except ValueError:
        log.warning("omada: réponse d'authentification non-JSON (HTTP %s)", r.status_code)
        return None

    if r.status_code != 200 or d.get("errorCode") != 0:
        # Ne jamais logger client_secret/accessToken : seul le code/msg Omada est tracé.
        log.warning(
            "omada: authentification refusée (HTTP %s, errorCode=%s, msg=%s)",
            r.status_code, d.get("errorCode"), d.get("msg"),
        )
        return None

    result = d.get("result") or {}
    token = result.get("accessToken")
    expires_in = result.get("expiresIn", 7200)
    if not token:
        log.warning("omada: authentification OK mais accessToken absent de la réponse")
        return None

    global _access_token, _token_expires_at
    with _lock:
        _access_token = token
        _token_expires_at = time.monotonic() + float(expires_in)
    log.info("omada: nouveau token obtenu (expire dans %ss)", expires_in)
    return token


def _ensure_token(cfg: dict, force: bool = False):
    with _lock:
        if not force and _access_token and time.monotonic() < _token_expires_at - _TOKEN_SAFETY_MARGIN_S:
            return _access_token
    return _request_new_token(cfg)


def _api_get(cfg: dict, path: str, params: dict = None):
    """GET authentifié vers l'API Omada. Retry une fois avec un token tout neuf
    si l'API renvoie un code d'erreur d'authentification. Retourne le dict JSON
    `result`, ou None si l'appel échoue définitivement."""
    token = _ensure_token(cfg)
    if not token:
        return None

    url = f"{cfg['connector_url']}{path}"
    for attempt in (1, 2):
        try:
            r = requests.get(
                url, params=params or {},
                headers={"Authorization": f"AccessToken={token}"},
                timeout=_HTTP_TIMEOUT,
            )
        except requests.RequestException as e:
            log.warning("omada: échec réseau sur %s (%s)", path, e)
            return None

        try:
            d = r.json()
        except ValueError:
            log.warning("omada: réponse non-JSON sur %s (HTTP %s)", path, r.status_code)
            return None

        err = d.get("errorCode")
        if err == 0:
            return d.get("result")

        if err in _TOKEN_ERROR_CODES and attempt == 1:
            log.info("omada: token invalide/expiré (errorCode=%s) sur %s — nouvel essai", err, path)
            _invalidate_token()
            token = _ensure_token(cfg, force=True)
            if not token:
                return None
            continue

        log.warning("omada: erreur API sur %s (errorCode=%s, msg=%s)", path, err, d.get("msg"))
        return None

    return None


def resolve_site_id(cfg: dict = None) -> str:
    """Retourne le site_id configuré, ou l'auto-détecte via /sites (premier site
    de la liste, ou celui dont le device_id est présent si plusieurs sites et
    que l'API de site le permet) puis le persiste en config."""
    cfg = cfg or _cfg()
    if cfg.get("site_id"):
        return cfg["site_id"]
    if not is_configured(cfg):
        return ""

    result = _api_get(cfg, f"/openapi/v1/{cfg['omadac_id']}/sites", {"page": 1, "pageSize": 50})
    if not result:
        return ""
    sites = result.get("data") or []
    if not sites:
        log.warning("omada: aucun site retourné par l'API")
        return ""
    site_id = sites[0].get("siteId", "")
    if site_id:
        db.set_config("omada_site_id", site_id)
        log.info("omada: site_id résolu et enregistré (%s site(s) disponible(s))", len(sites))
    return site_id


def test_connection() -> tuple:
    """Vérifie de bout en bout : auth + résolution de site + un petit appel de
    lecture. Utilisé par la route /api/config/test-omada. Ne lève jamais."""
    cfg = _cfg()
    if not is_configured(cfg):
        return False, "Configuration Omada incomplète (URL connecteur / Omada ID / Client ID / Client Secret requis)"

    _invalidate_token()
    token = _ensure_token(cfg, force=True)
    if not token:
        return False, "Authentification échouée — vérifiez les credentials et l'URL du connecteur (voir logs serveur pour le détail)"

    site_id = resolve_site_id(cfg)
    if not site_id:
        return False, "Authentification OK mais aucun site Omada trouvé"

    now_ms = int(time.time() * 1000)
    result = _api_get(
        cfg, f"/openapi/v1/{cfg['omadac_id']}/sites/{site_id}/logs/alerts",
        {"page": 1, "pageSize": 5, "filters.timeStart": now_ms - _INITIAL_LOOKBACK_MS, "filters.timeEnd": now_ms},
    )
    if result is None:
        return False, "Authentification et site OK, mais la lecture des logs d'alerte a échoué"

    total = result.get("totalRows", 0)
    return True, f"Connexion Omada OK — site {site_id[:8]}… — {total} événement(s) sur les 24 dernières heures"


def fetch_new_events(page_size: int = 100, max_pages: int = 5) -> int:
    """Récupère les événements d'alerte Omada depuis le dernier fetch connu et
    les insère dans `router_events`. Retourne le nombre de nouveaux événements
    insérés (0 si non configuré ou en cas d'erreur — jamais d'exception)."""
    try:
        cfg = _cfg()
        if not is_configured(cfg):
            return 0

        site_id = resolve_site_id(cfg)
        if not site_id:
            return 0

        now_ms = int(time.time() * 1000)
        last_ms = db.get_config("omada_last_event_ms", "")
        if last_ms:
            try:
                time_start = max(0, int(last_ms) - _OVERLAP_MS)
            except ValueError:
                time_start = now_ms - _INITIAL_LOOKBACK_MS
        else:
            time_start = now_ms - _INITIAL_LOOKBACK_MS

        inserted = 0
        max_time_seen = int(last_ms) if last_ms else 0
        page = 1
        while page <= max_pages:
            result = _api_get(
                cfg, f"/openapi/v1/{cfg['omadac_id']}/sites/{site_id}/logs/alerts",
                {"page": page, "pageSize": page_size,
                 "filters.timeStart": time_start, "filters.timeEnd": now_ms},
            )
            if result is None:
                break

            rows = result.get("data") or []
            for row in rows:
                t_ms = row.get("time") or 0
                ts_s = int(t_ms / 1000) if t_ms else int(time.time())
                level    = row.get("level", "") or ""
                category = row.get("module", "") or row.get("key", "") or ""
                message  = row.get("content", "") or ""
                event_id = row.get("id")
                if db.insert_router_event(ts_s, level, category, message, event_id, json.dumps(row, ensure_ascii=False)):
                    inserted += 1
                if t_ms and t_ms > max_time_seen:
                    max_time_seen = t_ms

            total_rows = result.get("totalRows", len(rows))
            if len(rows) < page_size or page * page_size >= total_rows:
                break
            page += 1

        if max_time_seen:
            db.set_config("omada_last_event_ms", str(max_time_seen))

        if inserted:
            log.info("omada: %d nouvel(aux) événement(s) routeur inséré(s)", inserted)
        return inserted

    except Exception as e:
        # Garde-fou ultime : cette fonction est appelée depuis le pool partagé
        # de monitor.py et ne doit jamais faire remonter d'exception.
        log.error("omada: échec inattendu de la collecte d'événements (%s)", e)
        return 0
