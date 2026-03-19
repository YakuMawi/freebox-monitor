"""
monitor.py — Freebox Monitor v1.0.0
Dashboard : https://<ip-serveur>:8000
"""
import json
import hmac
import hashlib
import time
import secrets
import threading
import sys
import os
import logging
import functools
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, render_template, request, Response, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

import db
import alerts as alert_mod
import updater

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
FREEBOX_URL      = "http://mafreebox.freebox.fr"
API_VERSION      = "v8"
CREDENTIALS_FILE = "credentials.json"
COLLECT_INTERVAL = 10
CONFIG_FILE      = "config.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SESSION_COOKIE_SECURE"]   = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    return response


# ──────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Non authentifié"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────
# État global (protégé par _lock)
# ──────────────────────────────────────────────
_lock              = threading.Lock()
_session_token     = None
_session_time      = 0
_metrics           = {}
_switch_data       = []
_storage_data      = {}
_current_outage_id = None
_last_conn_state   = None
_last_ipv4         = "?"
_last_prune        = 0
_test_mode_active  = False

# Rate limiting : {ip: [timestamp, ...]}
_rate_login        = {}
_rate_forgot       = {}
_rate_lock         = threading.Lock()

RATE_LOGIN_MAX     = 5    # tentatives
RATE_LOGIN_WINDOW  = 300  # secondes
RATE_FORGOT_MAX    = 3
RATE_FORGOT_WINDOW = 600


def _get_ip():
    # Never trust X-Forwarded-For unless behind a known reverse proxy.
    # For direct deployment (default), always use the real remote address.
    return request.remote_addr or "127.0.0.1"


def _is_rate_limited(store: dict, ip: str, max_attempts: int, window: int) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [t for t in store.get(ip, []) if now - t < window]
        store[ip] = hits
        if len(hits) >= max_attempts:
            return True
        store[ip].append(now)
        return False


# ──────────────────────────────────────────────
# Session Freebox
# ──────────────────────────────────────────────

def load_credentials():
    if not os.path.exists(CREDENTIALS_FILE):
        log.error("credentials.json introuvable — lancez auth.py")
        sys.exit(1)
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def api_url(path=""):
    return f"{FREEBOX_URL}/api/{API_VERSION}{path}"


def get_session():
    global _session_token, _session_time
    if _session_token and (time.time() - _session_time) < 1500:
        return _session_token
    creds     = load_credentials()
    r         = requests.get(api_url("/login/"), timeout=10)
    challenge = r.json()["result"]["challenge"]
    password  = hmac.new(
        creds["app_token"].encode(), challenge.encode(), hashlib.sha1
    ).hexdigest()
    r = requests.post(api_url("/login/session/"), json={
        "app_id": creds["app_id"], "password": password
    }, timeout=10)
    result = r.json()
    if not result["success"]:
        raise RuntimeError(f"Erreur session: {result}")
    _session_token = result["result"]["session_token"]
    _session_time  = time.time()
    return _session_token


def fbx_get(path):
    global _session_token, _session_time
    token = get_session()
    try:
        r = requests.get(api_url(path), headers={"X-Fbx-App-Auth": token}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        # Invalidate cached session so next cycle re-authenticates (e.g. after box reboot)
        _session_token = None
        _session_time  = 0
        raise
    if not data.get("success"):
        _session_token = None
        _session_time  = 0
        raise RuntimeError(f"API error {path}: {data.get('msg', data)}")
    return data["result"]


# ──────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────

def fmt_uptime(seconds):
    if not seconds:
        return "—"
    td = timedelta(seconds=seconds)
    d  = td.days
    h, rem = divmod(td.seconds, 3600)
    m  = rem // 60
    return f"{d}j {h}h{m:02d}m" if d else f"{h}h{m:02d}m"


def fmt_bytes(bps):
    bits = bps * 8
    if bits >= 1_000_000_000:
        return f"{bits / 1_000_000_000:.1f} Gbit/s"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f} Mbit/s"
    if bits >= 1_000:
        return f"{bits / 1_000:.0f} Kbit/s"
    return f"{bits} bit/s"


def fmt_gb(b):
    if b is None:
        return "—"
    if b >= 1e12:
        return f"{b/1e12:.2f} To"
    if b >= 1e9:
        return f"{b/1e9:.1f} Go"
    if b >= 1e6:
        return f"{b/1e6:.0f} Mo"
    return f"{b/1e3:.0f} Ko"


# ──────────────────────────────────────────────
# Collecteurs
# ──────────────────────────────────────────────

def collect_system():
    s    = fbx_get("/system/")
    sens = {item["id"]: item["value"] for item in s.get("sensors", [])}
    fans = {item["id"]: item["value"] for item in s.get("fans", [])}
    return {
        "board":      s.get("board_name", "Freebox"),
        "firmware":   s.get("firmware_version", ""),
        "uptime_val": s.get("uptime_val", 0),
        "uptime":     fmt_uptime(s.get("uptime_val", 0)),
        "disk_status": s.get("disk_status", ""),
        "sensors":    sens,
        "fans":       fans,
        # Champs aplatis pour compatibilité graphique
        "temp_cpu_cp_master": sens.get("temp_cpu_cp_master", 0),
        "temp_cpu_ap":        sens.get("temp_cpu_ap", 0),
        "temp_cpu_cp_slave":  sens.get("temp_cpu_cp_slave", 0),
        "temp_t1":            sens.get("temp_t1", 0),
        "temp_t2":            sens.get("temp_t2", 0),
        "temp_t3":            sens.get("temp_t3", 0),
        "temp_hdd0":          sens.get("temp_hdd0", 0),
        "fan0_speed":         fans.get("fan0_speed", 0),
        "fan1_speed":         fans.get("fan1_speed", 0),
    }


def collect_connection():
    c = fbx_get("/connection/")
    return {
        "state":          c.get("state", ""),
        "type":           c.get("type", ""),
        "media":          c.get("media", ""),
        "ipv4":           c.get("ipv4", ""),
        "ipv6":           c.get("ipv6", ""),
        "rate_down":      c.get("rate_down", 0),
        "rate_up":        c.get("rate_up", 0),
        "bandwidth_down": c.get("bandwidth_down", 0),
        "bandwidth_up":   c.get("bandwidth_up", 0),
        "bytes_down":     c.get("bytes_down", 0),
        "bytes_up":       c.get("bytes_up", 0),
        "rate_down_fmt":      fmt_bytes(c.get("rate_down", 0)),
        "rate_up_fmt":        fmt_bytes(c.get("rate_up", 0)),
        "bandwidth_down_fmt": fmt_bytes(c.get("bandwidth_down", 0)),
        "bandwidth_up_fmt":   fmt_bytes(c.get("bandwidth_up", 0)),
        "bytes_down_fmt":     fmt_gb(c.get("bytes_down", 0)),
        "bytes_up_fmt":       fmt_gb(c.get("bytes_up", 0)),
    }


def collect_ftth():
    try:
        f = fbx_get("/connection/ftth/")
        return {
            "sfp_present":    f.get("sfp_present", False),
            "sfp_has_signal": f.get("sfp_has_signal", False),
            "link":           f.get("link", False),
            "sfp_vendor":     f.get("sfp_vendor", ""),
            "sfp_model":      f.get("sfp_model", ""),
            "sfp_pwr_tx":     round(f.get("sfp_pwr_tx", 0) / 100, 2),
            "sfp_pwr_rx":     round(f.get("sfp_pwr_rx", 0) / 100, 2),
        }
    except Exception:
        return None


def collect_lan():
    try:
        interfaces = fbx_get("/lan/browser/interfaces/")
        total = 0
        for iface in interfaces:
            try:
                hosts  = fbx_get(f"/lan/browser/{iface['name']}/")
                total += sum(1 for h in hosts if h.get("reachable"))
            except Exception:
                pass
        return {"active_hosts": total}
    except Exception as e:
        return {"active_hosts": "?", "error": str(e)}


def collect_switch():
    try:
        ports = fbx_get("/switch/status/")
        result = []
        for port in ports:
            pid = port.get("id")
            stats = {}
            try:
                stats = fbx_get(f"/switch/port/{pid}/stats/")
            except Exception:
                pass
            result.append({
                "id":     pid,
                "name":   port.get("name", f"Port {pid}"),
                "link":   port.get("link", "down"),
                "speed":  port.get("speed", ""),
                "duplex": port.get("duplex", ""),
                "rx_bytes": stats.get("rx_bytes", 0),
                "tx_bytes": stats.get("tx_bytes", 0),
                "rx_bytes_rate": stats.get("rx_bytes_rate", 0),
                "tx_bytes_rate": stats.get("tx_bytes_rate", 0),
                "rx_err_packets": stats.get("rx_err_packets", 0),
                "tx_fcs":         stats.get("tx_fcs", 0),
            })
        return result
    except Exception as e:
        log.warning("collect_switch: %s", e)
        return []


def collect_storage():
    try:
        disks = fbx_get("/storage/disk/")
        partitions = fbx_get("/storage/partition/")
        disks_out = []
        for d in disks:
            disks_out.append({
                "id":       d.get("id"),
                "type":     d.get("type", ""),
                "state":    d.get("state", ""),
                "model":    d.get("model", ""),
                "serial":   d.get("serial", ""),
                "firmware": d.get("firmware", ""),
                "total_bytes": d.get("total_bytes", 0),
                "total_fmt":   fmt_gb(d.get("total_bytes", 0)),
                "temp":     d.get("temp", 0),
                "spinning": d.get("spinning", False),
                "idle":     d.get("idle", True),
            })
        parts_out = []
        for p in partitions:
            total = p.get("total_bytes", 0)
            free  = p.get("free_bytes", 0)
            used  = total - free
            parts_out.append({
                "id":          p.get("id"),
                "disk_id":     p.get("disk_id"),
                "label":       p.get("label", ""),
                "fstype":      p.get("fstype", ""),
                "state":       p.get("state", ""),
                "total_bytes": total,
                "free_bytes":  free,
                "used_bytes":  used,
                "total_fmt":   fmt_gb(total),
                "free_fmt":    fmt_gb(free),
                "used_fmt":    fmt_gb(used),
                "used_pct":    round(used / total * 100, 1) if total > 0 else 0,
            })
        return {"disks": disks_out, "partitions": parts_out}
    except Exception as e:
        log.warning("collect_storage: %s", e)
        return {"disks": [], "partitions": []}


def collect():
    m = {"collected_at": datetime.now().strftime("%H:%M:%S")}
    try:
        m["system"] = collect_system()
    except Exception as e:
        m["system"] = {"error": str(e)}
    try:
        m["connection"] = collect_connection()
    except Exception as e:
        m["connection"] = {"error": str(e), "state": "down"}
    m["ftth"] = collect_ftth()
    m["lan"]  = collect_lan()
    return m


# ──────────────────────────────────────────────
# Outage detection & alerting
# ──────────────────────────────────────────────

def process_connectivity(conn_state: str, ts: int):
    global _current_outage_id, _last_conn_state, _last_ipv4

    ipv4 = _metrics.get("connection", {}).get("ipv4", "?")
    if ipv4 and ipv4 != "?":
        _last_ipv4 = ipv4

    if _last_conn_state is None:
        _last_conn_state = conn_state
        return

    was_up  = _last_conn_state == "up"
    is_up   = conn_state == "up"

    if was_up and not is_up:
        # Transition up -> down
        test = _test_mode_active
        cause = "test volontaire" if test else "connexion perdue"
        oid = db.open_outage(ts, cause, is_test=1 if test else 0)
        _current_outage_id = oid
        log.warning("Perte de connexion détectée à %s (test=%s)", datetime.fromtimestamp(ts), test)
        if not test:
            _schedule_outage_alert(ts, _last_ipv4)

    elif not was_up and is_up:
        # Transition down -> up
        dur = db.close_outage(ts)
        _current_outage_id = None
        if dur:
            log.info("Connexion rétablie après %d s", dur)
            cfg = db.get_all_config()
            if cfg.get("alerts_enabled", "false").lower() == "true":
                threading.Thread(
                    target=alert_mod.send_recovery_alert,
                    args=(cfg, ts - dur, dur, ipv4),
                    daemon=True
                ).start()

    _last_conn_state = conn_state


def _schedule_outage_alert(started_at: int, ipv4: str):
    min_s = int(db.get_config("alert_outage_min_s", "30") or "30")

    def _delayed():
        time.sleep(min_s)
        cfg = db.get_all_config()
        if cfg.get("alerts_enabled", "false").lower() != "true":
            return
        # Check if outage still open
        outages = db.get_outages(limit=1)
        if outages and outages[0].get("ended_at") is None:
            alert_mod.send_outage_alert(cfg, started_at, ipv4)

    threading.Thread(target=_delayed, daemon=True).start()


# ──────────────────────────────────────────────
# Background collection loop
# ──────────────────────────────────────────────

def background_loop():
    global _metrics, _switch_data, _storage_data, _last_prune
    slow_counter = 0

    while True:
        try:
            data   = collect()
            sw     = _switch_data
            st     = _storage_data
            # slow refresh every 6 cycles (~60 s)
            if slow_counter % 6 == 0:
                try:
                    sw = collect_switch()
                except Exception as e:
                    log.warning("switch: %s", e)
                try:
                    st = collect_storage()
                except Exception as e:
                    log.warning("storage: %s", e)

            with _lock:
                _metrics      = data
                _switch_data  = sw
                _storage_data = st

            db.insert_metric(data)
            conn_state = data.get("connection", {}).get("state", "")
            process_connectivity(conn_state, int(datetime.now().timestamp()))

            # Weekly prune
            now = time.time()
            if now - _last_prune > 86400 * 7:
                db.prune_metrics(365)
                _last_prune = now

        except Exception as e:
            log.error("Erreur collecte: %s", e)
            with _lock:
                _metrics["error"] = str(e)

        slow_counter += 1
        time.sleep(COLLECT_INTERVAL)


# ──────────────────────────────────────────────
# Report generator
# ──────────────────────────────────────────────

def render_monthly_report(year: int, month: int) -> str:
    from calendar import monthrange
    month_names = ["Janvier","Février","Mars","Avril","Mai","Juin",
                   "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
    month_name = month_names[month - 1]
    since_ts   = int(datetime(year, month, 1).timestamp())
    stats      = db.get_period_stats(since_ts)
    outages    = db.get_outages(limit=200)
    cal_data   = db.get_daily_uptime(year, month)
    _, days    = monthrange(year, month)

    def color(pct):
        if pct is None: return "#2a2d3a"
        if pct >= 99:   return "#276749"
        if pct >= 95:   return "#48bb78"
        if pct >= 80:   return "#ed8936"
        return "#fc8181"

    def cell(day_str, pct, avg_down):
        bg = color(pct)
        label = f"{pct}%" if pct is not None else "—"
        tip = f"Uptime: {label}" + (f"\nDébit moy: {fmt_bytes(int(avg_down or 0))}" if avg_down else "")
        d = int(day_str.split("-")[2]) if day_str else 0
        return (f"<td title='{tip}' style='background:{bg};padding:8px;text-align:center;"
                f"color:#fff;font-size:12px;border-radius:4px;'><div style='font-weight:700'>{d}</div>"
                f"<div style='font-size:10px;opacity:.8'>{label}</div></td>")

    # Build calendar rows
    import calendar
    cal = calendar.monthcalendar(year, month)
    rows_html = ""
    for week in cal:
        rows_html += "<tr>"
        for day_num in week:
            if day_num == 0:
                rows_html += "<td style='padding:8px'></td>"
            else:
                day_str = f"{year}-{month:02d}-{day_num:02d}"
                d = cal_data.get(day_str, {})
                rows_html += cell(day_str, d.get("uptime_pct"), d.get("avg_down"))
        rows_html += "</tr>"

    outage_rows = ""
    for o in outages:
        if o.get("started_at", 0) < since_ts:
            continue
        outage_rows += (
            f"<tr><td>{o['started_fmt']}</td><td>{o['ended_fmt']}</td>"
            f"<td>{o['duration_fmt']}</td><td>{o.get('cause','—')}</td></tr>"
        )

    # Pré-calcul du tableau des coupures (évite f-string imbriqué, incompatible Python < 3.12)
    if outage_rows:
        outage_section = (
            "<table><tr><th>Début</th><th>Fin</th><th>Durée</th><th>Cause</th></tr>"
            + outage_rows
            + "</table>"
        )
    else:
        outage_section = "<p style='color:#718096;font-size:13px'>Aucune coupure ce mois.</p>"

    def stat(v, fmt_fn=None, unit=""):
        if v is None: return "—"
        return (fmt_fn(v) if fmt_fn else str(round(v, 1))) + unit

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<title>Rapport Freebox — {month_name} {year}</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#0f1117;color:#e2e8f0;padding:32px;}}
  h1{{color:#4299e1}} h2{{color:#4299e1;font-size:16px;margin-top:32px}}
  table{{width:100%;border-collapse:collapse;margin-top:12px}}
  th{{background:#1a1d27;padding:10px;text-align:left;font-size:13px;color:#718096}}
  td{{padding:8px 10px;border-bottom:1px solid #2a2d3a;font-size:13px}}
  .cal-header td{{background:#1a1d27;text-align:center;color:#718096;font-size:12px;padding:6px}}
  .summary-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:16px}}
  .stat-card{{background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;padding:16px}}
  .stat-val{{font-size:24px;font-weight:700;color:#4299e1}}
  .stat-lbl{{font-size:12px;color:#718096;margin-top:4px}}
  @media print{{body{{background:#fff;color:#000}} .stat-card{{border:1px solid #ccc}}
    table td,table th{{color:#000}} h1,h2{{color:#2b6cb0}} }}
  .btn{{display:inline-block;margin-bottom:24px;padding:10px 20px;background:#4299e1;
        color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:14px}}
</style>
</head><body>
<button class="btn" onclick="window.print()">🖨 Imprimer / Exporter PDF</button>
<h1>📡 Rapport Freebox — {month_name} {year}</h1>
<p style="color:#718096;font-size:13px">Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}</p>

<h2>Résumé</h2>
<div class="summary-grid">
  <div class="stat-card"><div class="stat-val">{stat(stats.get('uptime_pct'))}%</div><div class="stat-lbl">Disponibilité</div></div>
  <div class="stat-card"><div class="stat-val">{stats.get('outage_count',0)}</div><div class="stat-lbl">Coupures</div></div>
  <div class="stat-card"><div class="stat-val">{stats.get('outage_total_fmt','—')}</div><div class="stat-lbl">Durée totale coupures</div></div>
  <div class="stat-card"><div class="stat-val">{stat(stats.get('avg_down'), fmt_bytes)}</div><div class="stat-lbl">Débit descendant moyen</div></div>
  <div class="stat-card"><div class="stat-val">{stat(stats.get('max_down'), fmt_bytes)}</div><div class="stat-lbl">Débit descendant max</div></div>
  <div class="stat-card"><div class="stat-val">{stat(stats.get('avg_up'), fmt_bytes)}</div><div class="stat-lbl">Débit montant moyen</div></div>
  <div class="stat-card"><div class="stat-val">{stat(stats.get('delta_bytes_down'), fmt_gb)}</div><div class="stat-lbl">Données téléchargées</div></div>
  <div class="stat-card"><div class="stat-val">{stat(stats.get('delta_bytes_up'), fmt_gb)}</div><div class="stat-lbl">Données envoyées</div></div>
  <div class="stat-card"><div class="stat-val">{stat(stats.get('avg_temp'))}°C</div><div class="stat-lbl">Température CPU moy.</div></div>
</div>

<h2>Calendrier de disponibilité</h2>
<table>
  <tr class="cal-header"><td>Lun</td><td>Mar</td><td>Mer</td><td>Jeu</td><td>Ven</td><td>Sam</td><td>Dim</td></tr>
  {rows_html}
</table>

<h2>Journal des coupures</h2>
{outage_section}

</body></html>"""


# ──────────────────────────────────────────────
# Flask routes
# ──────────────────────────────────────────────

PERIOD_MAP = {
    "24h": 86400, "7j": 604800, "30j": 2592000,
    "60j": 5184000, "90j": 7776000, "1an": 31536000
}

ALLOWED_CONFIG_KEYS = {
    "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from",
    "smtp_tls", "smtp_ssl", "alert_to", "alerts_enabled", "alert_outage_min_s",
    "github_repo", "github_token", "port",
}


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if "user" in session:
        return redirect(url_for("index"))
    cfg = db.get_all_config()
    smtp_ok = bool(cfg.get("smtp_host", "").strip())
    if request.method == "POST":
        ip = _get_ip()
        if _is_rate_limited(_rate_forgot, ip, RATE_FORGOT_MAX, RATE_FORGOT_WINDOW):
            return render_template("forgot_password.html", smtp_ok=smtp_ok,
                                   error="Trop de tentatives. Réessayez dans 10 minutes.")
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip()
        user = db.get_user(username)
        if not user or not db.check_recovery_email(username, email):
            return render_template("forgot_password.html", smtp_ok=smtp_ok,
                                   error="Nom d'utilisateur ou email de récupération incorrect.")
        if not smtp_ok:
            # Pas de SMTP : email vérifié — on stocke l'autorisation côté serveur dans la session
            session["reset_allowed_user"] = username
            return redirect(url_for("reset_password"))
        code = db.create_reset_code(username)
        ok, msg = alert_mod.send_reset_code_email(cfg, email, username, code)
        if not ok:
            return render_template("forgot_password.html", smtp_ok=smtp_ok,
                                   error=f"Erreur d'envoi email : {msg}")
        session["reset_pending_user"] = username
        return redirect(url_for("reset_password", sent="1"))
    return render_template("forgot_password.html", smtp_ok=smtp_ok)


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if "user" in session:
        return redirect(url_for("index"))

    # Deux modes autorisés, tous deux vérifiés côté serveur :
    # - reset_allowed_user : identité vérifiée par email (sans SMTP)
    # - reset_pending_user : code OTP envoyé par SMTP
    no_smtp  = "reset_allowed_user" in session
    username = session.get("reset_allowed_user") or session.get("reset_pending_user", "")

    if not username:
        # Accès direct sans passer par /forgot-password → refusé
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_pw  = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not new_pw or not confirm:
            return render_template("reset_password.html", username=username, no_smtp=no_smtp,
                                   error="Tous les champs sont requis.")
        if new_pw != confirm:
            return render_template("reset_password.html", username=username, no_smtp=no_smtp,
                                   error="Les mots de passe ne correspondent pas.")
        if len(new_pw) < 4:
            return render_template("reset_password.html", username=username, no_smtp=no_smtp,
                                   error="Mot de passe trop court (min. 4 caractères).")
        if not no_smtp:
            code = request.form.get("code", "").strip()
            if not code:
                return render_template("reset_password.html", username=username, no_smtp=no_smtp,
                                       error="Le code est requis.")
            code_id = db.verify_reset_code(username, code)
            if code_id is None:
                return render_template("reset_password.html", username=username, no_smtp=no_smtp,
                                       error="Code invalide ou expiré.")
            db.consume_reset_code(code_id)
        db.update_password(username, generate_password_hash(new_pw))
        session.pop("reset_allowed_user", None)
        session.pop("reset_pending_user", None)
        return render_template("login.html", success="Mot de passe modifié. Vous pouvez vous connecter.")

    sent = request.args.get("sent", "")
    return render_template("reset_password.html", username=username, no_smtp=no_smtp, sent=sent)


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db.user_count() > 0:
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "admin").strip()
        password = request.form.get("password", "")
        if not username or not password:
            return render_template("setup.html", error="Tous les champs sont requis")
        if len(password) < 4:
            return render_template("setup.html", error="Mot de passe trop court (min. 4 caractères)")
        db.create_user(username, generate_password_hash(password))
        recovery_email = request.form.get("recovery_email", "").strip()
        if recovery_email:
            db.set_recovery_email(username, recovery_email)
        session["user"] = username
        return redirect(url_for("index"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if db.user_count() == 0:
        return redirect(url_for("setup"))
    if request.method == "POST":
        ip = _get_ip()
        if _is_rate_limited(_rate_login, ip, RATE_LOGIN_MAX, RATE_LOGIN_WINDOW):
            return render_template("login.html", error="Trop de tentatives. Réessayez dans quelques minutes.")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user(username)
        if user and check_password_hash(user["password"], password):
            session["user"] = username
            return redirect(url_for("index"))
        return render_template("login.html", error="Identifiants incorrects")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/metrics")
@login_required
def route_metrics():
    with _lock:
        return jsonify(_metrics)


@app.route("/api/history")
@login_required
def route_history():
    seconds = min(int(request.args.get("seconds", 600)), 86400)
    return jsonify(db.get_history(seconds))


@app.route("/api/stats")
@login_required
def route_stats():
    period   = request.args.get("period", "24h")
    delta_s  = PERIOD_MAP.get(period, 86400)
    since_ts = int((datetime.now() - timedelta(seconds=delta_s)).timestamp())
    return jsonify(db.get_period_stats(since_ts))


@app.route("/api/outages")
@login_required
def route_outages():
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    return jsonify(db.get_outages(limit, offset))


@app.route("/api/outages/reset-days", methods=["POST"])
@login_required
def route_reset_outage_days():
    data  = request.get_json(force=True) or {}
    dates = data.get("dates", [])
    if not isinstance(dates, list) or not dates:
        return jsonify({"ok": False, "msg": "dates doit être une liste non vide"}), 400
    db.reset_outages_by_days(dates)
    return jsonify({"ok": True, "msg": f"{len(dates)} jour(s) réinitialisé(s)"})


@app.route("/api/outages/<int:outage_id>", methods=["PATCH"])
@login_required
def route_patch_outage(outage_id):
    data    = request.get_json(force=True) or {}
    is_test = int(bool(data["is_test"])) if "is_test" in data else None
    note    = data.get("note")   # None = ne pas modifier la note
    db.mark_outage(outage_id, is_test, note)
    return jsonify({"ok": True})


@app.route("/api/test-mode", methods=["GET"])
@login_required
def route_test_mode_get():
    with _lock:
        return jsonify({"active": _test_mode_active})


@app.route("/api/test-mode", methods=["POST"])
@login_required
def route_test_mode_set():
    global _test_mode_active
    data = request.get_json(force=True) or {}
    with _lock:
        _test_mode_active = bool(data.get("active", False))
    log.info("Mode test %s", "activé" if _test_mode_active else "désactivé")
    return jsonify({"active": _test_mode_active})


@app.route("/api/calendar")
@login_required
def route_calendar():
    now   = datetime.now()
    year  = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    return jsonify(db.get_daily_uptime(year, month))


@app.route("/api/switch")
@login_required
def route_switch():
    with _lock:
        return jsonify(_switch_data)


@app.route("/api/storage")
@login_required
def route_storage():
    with _lock:
        return jsonify(_storage_data)


@app.route("/api/config", methods=["GET"])
@login_required
def route_config_get():
    cfg = db.get_all_config()
    # Redact secrets
    if "smtp_password" in cfg:
        cfg["smtp_password"] = "••••••••" if cfg["smtp_password"] else ""
    if "github_token" in cfg:
        cfg["github_token"] = "••••••••" if cfg["github_token"] else ""
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
@login_required
def route_config_post():
    data = request.get_json(force=True) or {}
    for key, value in data.items():
        if key not in ALLOWED_CONFIG_KEYS:
            continue  # Ignore unknown / sensitive keys
        if key in ("smtp_password", "github_token") and value == "••••••••":
            continue  # Don't overwrite with redacted placeholder
        db.set_config(key, str(value))
    return jsonify({"ok": True})


@app.route("/api/config/test-email", methods=["POST"])
@login_required
def route_test_email():
    cfg = db.get_all_config()
    ok, msg = alert_mod.send_test_email(cfg)
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/report")
@login_required
def route_report():
    now   = datetime.now()
    year  = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    html  = render_monthly_report(year, month)
    return Response(html, mimetype="text/html")


@app.route("/api/auth/recovery-email", methods=["GET"])
@login_required
def route_get_recovery_email():
    return jsonify({"email": db.get_recovery_email(session["user"])})


@app.route("/api/auth/recovery-email", methods=["POST"])
@login_required
def route_set_recovery_email():
    data  = request.get_json(force=True) or {}
    email = data.get("email", "").strip()
    db.set_recovery_email(session["user"], email)
    return jsonify({"ok": True, "msg": "Email de récupération enregistré"})


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def route_change_password():
    data = request.get_json(force=True) or {}
    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    if not current or not new_pw:
        return jsonify({"ok": False, "msg": "Champs requis"}), 400
    user = db.get_user(session["user"])
    if not user or not check_password_hash(user["password"], current):
        return jsonify({"ok": False, "msg": "Mot de passe actuel incorrect"}), 403
    if len(new_pw) < 4:
        return jsonify({"ok": False, "msg": "Mot de passe trop court (min. 4 caractères)"}), 400
    db.update_password(session["user"], generate_password_hash(new_pw))
    return jsonify({"ok": True, "msg": "Mot de passe modifié"})


@app.route("/api/version")
@login_required
def route_version():
    return jsonify({"version": updater.get_current_version()})


@app.route("/api/update/check")
@login_required
def route_update_check():
    repo = db.get_config("github_repo", "")
    token = db.get_config("github_token", "")
    if not repo:
        return jsonify({"error": "Dépôt GitHub non configuré"}), 400
    return jsonify(updater.check_for_update(repo, token or None))


@app.route("/api/update/releases")
@login_required
def route_update_releases():
    repo = db.get_config("github_repo", "")
    token = db.get_config("github_token", "")
    if not repo:
        return jsonify({"error": "Dépôt GitHub non configuré"}), 400
    return jsonify(updater.list_releases(repo, token or None))


@app.route("/api/update/apply", methods=["POST"])
@login_required
def route_update_apply():
    data  = request.get_json(force=True) or {}
    tag   = data.get("tag") or None   # None = dernière version, sinon tag spécifique
    repo  = db.get_config("github_repo", "")
    token = db.get_config("github_token", "")
    if not repo:
        return jsonify({"ok": False, "msg": "Dépôt GitHub non configuré"}), 400
    ok, msg = updater.apply_update(repo, token or None, tag=tag)
    if ok:
        threading.Thread(target=_restart_service, daemon=True).start()
    return jsonify({"ok": ok, "msg": msg})


def _restart_service():
    """Attempt to restart the systemd service after update."""
    time.sleep(2)
    os.system("systemctl restart freebox-monitor.service 2>/dev/null || true")


# ──────────────────────────────────────────────
# Démarrage
# ──────────────────────────────────────────────

if __name__ == "__main__":
    load_credentials()
    db.init_db()

    # Seed config depuis config.json
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            defaults = json.load(f)
        db.seed_config(defaults)

    # Flask secret key (persistent)
    secret = db.get_config("flask_secret_key")
    if not secret:
        secret = secrets.token_hex(32)
        db.set_config("flask_secret_key", secret)
    app.secret_key = secret

    log.info("Connexion à la Freebox...")
    try:
        data = collect()
        sw   = collect_switch()
        st   = collect_storage()
        with _lock:
            _metrics      = data
            _switch_data  = sw
            _storage_data = st
        db.insert_metric(data)
        sys_info  = data.get("system", {})
        conn_info = data.get("connection", {})
        log.info("Connecté — %s firmware %s", sys_info.get("board"), sys_info.get("firmware"))
        log.info("Connexion %s — IP %s", conn_info.get("type"), conn_info.get("ipv4"))
    except Exception as e:
        log.warning("Collecte initiale échouée: %s", e)

    t = threading.Thread(target=background_loop, daemon=True)
    t.start()

    # Port (configurable via config.json ou DB)
    port = int(db.get_config("port", "8000") or "8000")

    # SSL
    cert_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "cert.pem")
    key_path  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        log.info("Dashboard : https://0.0.0.0:%d (SSL)", port)
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False,
                ssl_context=(cert_path, key_path))
    else:
        log.warning("Certificats SSL non trouvés dans certs/ — démarrage en HTTP")
        log.info("Dashboard : http://0.0.0.0:%d", port)
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
