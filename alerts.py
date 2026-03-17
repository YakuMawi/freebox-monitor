"""
alerts.py — Alertes SMTP pour Freebox Monitor.
"""
import smtplib
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


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
      <h2 style="margin:0;color:#fff;font-size:18px;">📡 Freebox Monitor</h2>
      <p style="margin:4px 0 0;color:rgba(255,255,255,.8);font-size:14px;">{title}</p>
    </div>
    <table style="width:100%;border-collapse:collapse;">{rows_html}</table>
    <div style="padding:12px 24px;border-top:1px solid #2a2d3a;
                font-size:11px;color:#718096;text-align:right;">
      Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}
    </div>
  </div>
</body></html>"""


def _send(config: dict, subject: str, html: str) -> tuple:
    host     = config.get("smtp_host", "")
    port     = int(config.get("smtp_port", 587))
    user     = config.get("smtp_user", "")
    password = config.get("smtp_password", "")
    from_    = config.get("smtp_from", "") or user
    to_      = config.get("alert_to", "")
    tls      = str(config.get("smtp_tls", "true")).lower() == "true"
    ssl      = str(config.get("smtp_ssl", "false")).lower() == "true"

    if not host or not to_:
        return False, "SMTP non configuré (host ou destinataire manquant)"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = from_
        msg["To"]      = to_
        msg.attach(MIMEText(html, "html"))

        if ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if tls:
                server.starttls()

        if user and password:
            server.login(user, password)

        server.sendmail(from_, to_.split(","), msg.as_string())
        server.quit()
        return True, "OK"
    except Exception as e:
        log.error("Erreur SMTP: %s", e)
        return False, str(e)


def send_outage_alert(config: dict, started_at: int, ipv4: str = "?") -> tuple:
    dt = datetime.fromtimestamp(started_at)
    subject = f"[Freebox] Perte de connexion le {dt.strftime('%d/%m/%Y à %H:%M')}"
    html = _build_html(
        "Perte de connectivité détectée",
        [
            ("Date/heure",        dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Adresse IP avant",  ipv4),
            ("Statut",            "Connexion perdue"),
        ],
        color="#fc8181"
    )
    return _send(config, subject, html)


def send_recovery_alert(config: dict, started_at: int, duration_s: int, ipv4: str = "?") -> tuple:
    from db import _fmt_dur
    dt = datetime.now()
    subject = f"[Freebox] Connexion rétablie — durée coupure : {_fmt_dur(duration_s)}"
    start_dt = datetime.fromtimestamp(started_at)
    html = _build_html(
        "Connexion rétablie",
        [
            ("Début coupure",      start_dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Fin coupure",        dt.strftime("%d/%m/%Y %H:%M:%S")),
            ("Durée",              _fmt_dur(duration_s)),
            ("Nouvelle IP",        ipv4),
        ],
        color="#48bb78"
    )
    return _send(config, subject, html)


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
