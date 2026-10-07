"""
alerts.py — Alertes SMTP & Webhooks pour Freebox Monitor.
"""
import json
import logging
import smtplib
import concurrent.futures
import urllib.parse
import urllib.request

from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import crypto

log = logging.getLogger(__name__)

# Pool partagé et borné pour l'envoi des webhooks (jusqu'à 5 par alerte) au lieu
# d'un threading.Thread ad-hoc par webhook et par alerte.
_webhook_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=5, thread_name_prefix="fbxmon-webhook"
)


_LOGO_HTML = """
<table style="border-collapse:collapse;margin-bottom:6px;"><tr>
  <td style="vertical-align:middle;">
    <table style="border-collapse:collapse;background:#1a1a2e;border-radius:8px;padding:0;"><tr>
      <td style="padding:6px 4px 6px 10px;vertical-align:middle;">
        <span style="font-family:Arial Black,Arial,sans-serif;font-weight:900;font-size:22px;color:#e94560;line-height:1;">F</span>
      </td>
      <td style="padding:6px 0;vertical-align:middle;">
        <div style="width:8px;height:1px;background:#e94560;opacity:.5;display:inline-block;vertical-align:middle;"></div>
      </td>
      <td style="padding:6px 10px 6px 0;vertical-align:middle;">
        <table style="border-collapse:collapse;"><tr>
          <td style="padding:3px 0;"><div style="width:5px;height:5px;background:#e94560;opacity:.4;border-radius:50%;"></div></td>
        </tr><tr>
          <td style="padding:3px 0;"><div style="width:5px;height:5px;background:#e94560;opacity:.65;border-radius:50%;"></div></td>
        </tr><tr>
          <td style="padding:3px 0;"><div style="width:5px;height:5px;background:#e94560;opacity:.9;border-radius:50%;"></div></td>
        </tr></table>
      </td>
    </tr></table>
  </td>
  <td style="padding-left:12px;vertical-align:middle;">
    <span style="color:#ffffff;font-size:20px;font-weight:700;font-family:Arial,sans-serif;letter-spacing:.3px;">Freebox Monitor</span>
  </td>
</tr></table>
"""


def _build_html(title: str, rows: list, color: str = "#4299e1") -> str:
    rows_html = "".join(
        f"<tr><td style='padding:8px 12px;color:#718096;'>{k}</td>"
        f"<td style='padding:8px 12px;font-weight:600;color:#e2e8f0;'>{v}</td></tr>"
        for k, v in rows
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#0f1117;font-family:system-ui,sans-serif;padding:32px;">
  <div style="max-width:520px;margin:0 auto;background:#1a1d27;border-radius:12px;
              border:1px solid #2a2d3a;overflow:hidden;">
    <div style="background:{color};padding:20px 24px;">
      {_LOGO_HTML}
      <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:14px;">{title}</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">{rows_html}</table>
    <div style="padding:12px 24px;border-top:1px solid #2a2d3a;
                font-size:11px;color:#718096;text-align:right;">
      Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}
    </div>
  </div>
</body></html>"""


# ─────────────────────────────────────
# SMTP
# ─────────────────────────────────────

def _smtp_settings(config: dict) -> dict:
    """Extrait et normalise les paramètres SMTP communs depuis la config."""
    user = config.get("smtp_user", "")
    return {
        "host":     config.get("smtp_host", ""),
        "port":     int(config.get("smtp_port", 587)),
        "user":     user,
        "password": crypto.decrypt(config.get("smtp_password", "")),
        "from_":    config.get("smtp_from", "") or user,
        "tls":      str(config.get("smtp_tls", "true")).lower() == "true",
        "ssl":      str(config.get("smtp_ssl", "false")).lower() == "true",
    }


def _smtp_deliver(settings: dict, recipients: list, subject: str, html: str,
                   log_label: str = "SMTP") -> tuple:
    """Connexion/TLS/login/sendmail — logique commune réutilisée par tous les
    envois d'email (alertes et code de réinitialisation)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = settings["from_"]
        msg["To"]      = ", ".join(recipients)
        msg.attach(MIMEText(html, "html"))

        if settings["ssl"]:
            server = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=15)
        else:
            server = smtplib.SMTP(settings["host"], settings["port"], timeout=15)
            if settings["tls"]:
                server.starttls()

        if settings["user"] and settings["password"]:
            server.login(settings["user"], settings["password"])

        server.sendmail(settings["from_"], recipients, msg.as_string())
        server.quit()
        return True, "OK"
    except Exception as e:
        log.error("Erreur %s: %s", log_label, e)
        return False, "Erreur d'envoi email. Vérifiez la configuration SMTP (logs serveur pour détails)."


def _send(config: dict, subject: str, html: str) -> tuple:
    settings = _smtp_settings(config)
    to_ = config.get("alert_to", "")

    if not settings["host"] or not to_:
        return False, "SMTP non configuré (host ou destinataire manquant)"

    recipients = [r.strip() for r in to_.split(",") if r.strip()]
    if not recipients:
        return False, "Aucun destinataire valide"

    return _smtp_deliver(settings, recipients, subject, html)


# ─────────────────────────────────────
# Webhooks
# ─────────────────────────────────────

def _post_json(url: str, payload: dict, timeout: int = 10) -> tuple:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as e:
        log.error("Webhook POST error (%s): %s", url[:40], e)
        return False, "Erreur d'envoi webhook (logs serveur pour détails)."


def _send_discord(url: str, title: str, description: str, color: int) -> tuple:
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": "Freebox Monitor"},
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }]
    }
    return _post_json(url, payload)


def _send_google_chat(url: str, title: str, message: str) -> tuple:
    payload = {
        "cards": [{
            "header": {"title": "Freebox Monitor", "subtitle": title},
            "sections": [{"widgets": [{"textParagraph": {"text": message}}]}]
        }]
    }
    return _post_json(url, payload)


def _send_teams(url: str, title: str, message: str, color_hint: str) -> tuple:
    # color_hint: "red" | "green" | "blue"
    color_map = {"red": "Attention", "green": "Good", "blue": "Accent"}
    card_color = color_map.get(color_hint, "Accent")
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.2",
                "body": [
                    {"type": "TextBlock", "text": "Freebox Monitor",
                     "weight": "Bolder", "size": "Medium"},
                    {"type": "TextBlock", "text": title,
                     "color": card_color, "weight": "Bolder"},
                    {"type": "TextBlock", "text": message, "wrap": True},
                ]
            }
        }]
    }
    return _post_json(url, payload)


def _send_synology(url: str, title: str, message: str) -> tuple:
    text = f"*{title}*\n{message}"
    try:
        data = urllib.parse.urlencode(
            {"payload": json.dumps({"text": text})}
        ).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as e:
        log.error("Synology webhook error: %s", e)
        return False, "Erreur d'envoi webhook Synology (logs serveur pour détails)."


def _send_generic(url: str, event: str, title: str, message: str) -> tuple:
    payload = {
        "event": event,
        "title": title,
        "message": message,
        "timestamp": datetime.now().isoformat(),
        "source": "freebox-monitor",
    }
    return _post_json(url, payload)


def _dispatch_webhooks(config: dict, event: str, title: str, message: str,
                       discord_color: int, teams_color: str):
    """Lance tous les webhooks configurés en arrière-plan (pool partagé borné)."""
    if str(config.get("webhooks_enabled", "false")).lower() != "true":
        return

    url = config.get("webhook_discord", "").strip()
    if url:
        _webhook_executor.submit(_send_discord, url, title, message, discord_color)

    url = config.get("webhook_google_chat", "").strip()
    if url:
        _webhook_executor.submit(_send_google_chat, url, title, message)

    url = config.get("webhook_teams", "").strip()
    if url:
        _webhook_executor.submit(_send_teams, url, title, message, teams_color)

    url = config.get("webhook_synology", "").strip()
    if url:
        _webhook_executor.submit(_send_synology, url, title, message)

    url = config.get("webhook_generic", "").strip()
    if url:
        _webhook_executor.submit(_send_generic, url, event, title, message)


# ─────────────────────────────────────
# Alertes publiques
# ─────────────────────────────────────

def send_outage_alert(config: dict, started_at: int, ipv4: str = "?") -> tuple:
    dt = datetime.fromtimestamp(started_at)
    title   = "Perte de connexion détectée"
    message = (f"Date/heure : {dt.strftime('%d/%m/%Y %H:%M:%S')}\n"
               f"IP avant : {ipv4}\nStatut : Connexion perdue")
    subject = f"[Freebox] Perte de connexion le {dt.strftime('%d/%m/%Y à %H:%M')}"
    html = _build_html(
        title,
        [
            ("Date/heure",       dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Adresse IP avant", ipv4),
            ("Statut",           "Connexion perdue"),
        ],
        color="#fc8181"
    )
    _dispatch_webhooks(config, "outage", title, message,
                       discord_color=16548225, teams_color="red")
    return _send(config, subject, html)


def send_recovery_alert(config: dict, started_at: int, duration_s: int, ipv4: str = "?") -> tuple:
    from format_utils import fmt_dur as _fmt_dur
    dt       = datetime.now()
    start_dt = datetime.fromtimestamp(started_at)
    dur_str  = _fmt_dur(duration_s)
    title    = "Connexion rétablie"
    message  = (f"Début coupure : {start_dt.strftime('%d/%m/%Y %H:%M:%S')}\n"
                f"Fin coupure : {dt.strftime('%d/%m/%Y %H:%M:%S')}\n"
                f"Durée : {dur_str}\nNouvelle IP : {ipv4}")
    subject  = f"[Freebox] Connexion rétablie — durée coupure : {dur_str}"
    html = _build_html(
        title,
        [
            ("Début coupure", start_dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Fin coupure",   dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Durée",         dur_str),
            ("Nouvelle IP",   ipv4),
        ],
        color="#48bb78"
    )
    _dispatch_webhooks(config, "recovery", title, message,
                       discord_color=4766072, teams_color="green")
    return _send(config, subject, html)


def send_ip_change_alert(config: dict, old_ip: str, new_ip: str) -> tuple:
    dt    = datetime.now()
    title = "Changement d'adresse IP publique"
    message = (f"Date/heure : {dt.strftime('%d/%m/%Y %H:%M:%S')}\n"
               f"Ancienne IP : {old_ip}\nNouvelle IP : {new_ip}")
    subject = f"[Freebox] Nouvelle IP publique : {new_ip}"
    html = _build_html(
        title,
        [
            ("Date/heure",   dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Ancienne IP",  old_ip),
            ("Nouvelle IP",  new_ip),
            ("Statut",       "Connexion maintenue"),
        ],
        color="#9f7aea"
    )
    _dispatch_webhooks(config, "ip_change", title, message,
                       discord_color=10435071, teams_color="purple")
    return _send(config, subject, html)


def send_reset_code_email(config: dict, to_email: str, username: str, code: str) -> tuple:
    """Envoie le code OTP de réinitialisation directement à l'adresse de récupération."""
    settings = _smtp_settings(config)

    if not settings["host"] or not to_email:
        return False, "SMTP non configuré"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#0f1117;font-family:system-ui,sans-serif;padding:32px;">
  <div style="max-width:520px;margin:0 auto;background:#1a1d27;border-radius:12px;
              border:1px solid #2a2d3a;overflow:hidden;">
    <div style="background:#4299e1;padding:20px 24px;">
      {_LOGO_HTML}
      <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:14px;">Réinitialisation de mot de passe</p>
    </div>
    <div style="padding:24px;">
      <p style="color:#e2e8f0;margin-bottom:16px;">Bonjour <strong>{username}</strong>,</p>
      <p style="color:#718096;font-size:14px;margin-bottom:24px;">
        Voici votre code de réinitialisation. Il est valable <strong style="color:#e2e8f0;">15 minutes</strong>.
      </p>
      <div style="background:#0f1117;border:2px solid #4299e1;border-radius:12px;
                  text-align:center;padding:24px;margin-bottom:24px;">
        <div style="font-size:42px;font-weight:700;color:#4299e1;letter-spacing:10px;">{code}</div>
      </div>
      <p style="color:#718096;font-size:12px;">
        Si vous n'avez pas demandé cette réinitialisation, ignorez cet email.
      </p>
    </div>
    <div style="padding:12px 24px;border-top:1px solid #2a2d3a;
                font-size:11px;color:#718096;text-align:right;">
      Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}
    </div>
  </div>
</body></html>"""

    return _smtp_deliver(
        settings, [to_email],
        "[Freebox Monitor] Code de réinitialisation de mot de passe",
        html,
        log_label="SMTP reset code",
    )


def send_test_email(config: dict) -> tuple:
    dt = datetime.now()
    html = _build_html(
        "Email de test — configuration SMTP OK",
        [
            ("Date/heure", dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Statut",     "Configuration valide"),
        ],
        color="#4299e1"
    )
    return _send(config, "[Freebox Monitor] Test SMTP", html)


def send_test_webhook(config: dict, webhook_type: str) -> tuple:
    dt      = datetime.now()
    title   = "Test Webhook — Freebox Monitor"
    message = f"Test envoyé le {dt.strftime('%d/%m/%Y à %H:%M:%S')} — Configuration valide"

    url_map = {
        "discord":     config.get("webhook_discord", "").strip(),
        "google_chat": config.get("webhook_google_chat", "").strip(),
        "teams":       config.get("webhook_teams", "").strip(),
        "synology":    config.get("webhook_synology", "").strip(),
        "generic":     config.get("webhook_generic", "").strip(),
    }

    url = url_map.get(webhook_type, "")
    if not url:
        return False, f"URL non configurée pour '{webhook_type}'"

    if webhook_type == "discord":
        return _send_discord(url, title, message, 4365537)
    elif webhook_type == "google_chat":
        return _send_google_chat(url, title, message)
    elif webhook_type == "teams":
        return _send_teams(url, title, message, "blue")
    elif webhook_type == "synology":
        return _send_synology(url, title, message)
    elif webhook_type == "generic":
        return _send_generic(url, "test", title, message)
    else:
        return False, f"Type de webhook inconnu : {webhook_type}"
