"""
db.py — Couche SQLite pour Freebox Monitor.
"""
import sqlite3
import os
from datetime import datetime, timedelta
from calendar import monthrange
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "freebox.db")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS metrics (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              INTEGER NOT NULL,
                conn_state      TEXT    DEFAULT '',
                rate_down       INTEGER DEFAULT 0,
                rate_up         INTEGER DEFAULT 0,
                bw_down         INTEGER DEFAULT 0,
                bw_up           INTEGER DEFAULT 0,
                bytes_down      INTEGER DEFAULT 0,
                bytes_up        INTEGER DEFAULT 0,
                temp_hdd0       REAL    DEFAULT 0,
                temp_t1         REAL    DEFAULT 0,
                temp_t2         REAL    DEFAULT 0,
                temp_t3         REAL    DEFAULT 0,
                temp_cpu_master REAL    DEFAULT 0,
                temp_cpu_ap     REAL    DEFAULT 0,
                temp_cpu_slave  REAL    DEFAULT 0,
                fan0_speed      INTEGER DEFAULT 0,
                fan1_speed      INTEGER DEFAULT 0,
                active_hosts    INTEGER DEFAULT 0,
                uptime_val      INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS outages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  INTEGER NOT NULL,
                ended_at    INTEGER,
                duration_s  INTEGER,
                cause       TEXT    DEFAULT 'connexion perdue'
            );
            CREATE TABLE IF NOT EXISTS config (
                key     TEXT PRIMARY KEY,
                value   TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT UNIQUE NOT NULL,
                password   TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_ts   ON metrics(ts);
            CREATE INDEX IF NOT EXISTS idx_outages_start ON outages(started_at);
        """)


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def insert_metric(data: dict):
    m = data
    conn = m.get("connection", {})
    sys  = m.get("system", {})
    lan  = m.get("lan", {})
    sens = sys.get("sensors", {})
    fans = sys.get("fans", {})
    hosts = lan.get("active_hosts", 0)
    if not isinstance(hosts, int):
        hosts = 0
    ts = int(datetime.now().timestamp())
    with _conn() as c:
        c.execute("""
            INSERT INTO metrics
            (ts, conn_state, rate_down, rate_up, bw_down, bw_up,
             bytes_down, bytes_up, temp_hdd0, temp_t1, temp_t2, temp_t3,
             temp_cpu_master, temp_cpu_ap, temp_cpu_slave,
             fan0_speed, fan1_speed, active_hosts, uptime_val)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ts,
            conn.get("state", ""),
            conn.get("rate_down", 0),    conn.get("rate_up", 0),
            conn.get("bandwidth_down", 0), conn.get("bandwidth_up", 0),
            conn.get("bytes_down", 0),   conn.get("bytes_up", 0),
            sens.get("temp_hdd0", 0),
            sens.get("temp_t1", 0),      sens.get("temp_t2", 0),
            sens.get("temp_t3", 0),
            sens.get("temp_cpu_cp_master", 0),
            sens.get("temp_cpu_ap", 0),
            sens.get("temp_cpu_cp_slave", 0),
            fans.get("fan0_speed", 0),   fans.get("fan1_speed", 0),
            hosts,
            sys.get("uptime_val", 0),
        ))


def get_history(seconds: int = 600) -> list:
    since = int((datetime.now() - timedelta(seconds=seconds)).timestamp())
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM metrics WHERE ts >= ? ORDER BY ts ASC", (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_period_stats(since_ts: int) -> dict:
    with _conn() as c:
        row = c.execute("""
            SELECT
                COUNT(*)                                           AS samples,
                SUM(CASE WHEN conn_state='up' THEN 1 ELSE 0 END)  AS up_samples,
                AVG(rate_down)   AS avg_down,  MAX(rate_down) AS max_down,
                AVG(rate_up)     AS avg_up,    MAX(rate_up)   AS max_up,
                AVG(active_hosts) AS avg_hosts, MAX(active_hosts) AS max_hosts,
                MAX(bytes_down) - MIN(bytes_down) AS delta_bytes_down,
                MAX(bytes_up)   - MIN(bytes_up)   AS delta_bytes_up,
                AVG(temp_cpu_master) AS avg_temp, MAX(temp_cpu_master) AS max_temp
            FROM metrics WHERE ts >= ?
        """, (since_ts,)).fetchone()
        out_row = c.execute("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(duration_s), 0) AS total_s
            FROM outages
            WHERE started_at >= ? AND ended_at IS NOT NULL
        """, (since_ts,)).fetchone()
    result = dict(row) if row else {}
    result["outage_count"]      = out_row["cnt"]   if out_row else 0
    result["outage_total_s"]    = out_row["total_s"] if out_row else 0
    result["outage_total_fmt"]  = _fmt_dur(result.get("outage_total_s", 0))
    samples = result.get("samples") or 0
    up_s    = result.get("up_samples") or 0
    result["uptime_pct"] = round(up_s / samples * 100, 2) if samples > 0 else None
    return result


def get_daily_uptime(year: int, month: int) -> dict:
    _, days = monthrange(year, month)
    start_dt = datetime(year, month, 1)
    end_dt   = datetime(year, month, days, 23, 59, 59)
    with _conn() as c:
        rows = c.execute("""
            SELECT
                DATE(ts, 'unixepoch', 'localtime')                AS day,
                COUNT(*)                                           AS total,
                SUM(CASE WHEN conn_state='up' THEN 1 ELSE 0 END)  AS up_cnt,
                AVG(rate_down)   AS avg_down,
                AVG(rate_up)     AS avg_up,
                MAX(rate_down)   AS max_down,
                AVG(temp_cpu_master) AS avg_temp
            FROM metrics
            WHERE ts >= ? AND ts <= ?
            GROUP BY DATE(ts, 'unixepoch', 'localtime')
            ORDER BY day
        """, (int(start_dt.timestamp()), int(end_dt.timestamp()))).fetchall()
    result = {}
    for r in rows:
        d = dict(r)
        d["uptime_pct"] = round(d["up_cnt"] / d["total"] * 100, 1) if d["total"] > 0 else 0
        result[d["day"]] = d
    return result


def get_daily_stats(days: int = 90) -> list:
    since = int((datetime.now() - timedelta(days=days)).timestamp())
    with _conn() as c:
        rows = c.execute("""
            SELECT
                DATE(ts, 'unixepoch', 'localtime') AS day,
                COUNT(*) AS samples,
                SUM(CASE WHEN conn_state='up' THEN 1 ELSE 0 END) AS up_cnt,
                AVG(rate_down) AS avg_down, MAX(rate_down) AS max_down,
                AVG(rate_up)   AS avg_up,   MAX(rate_up)   AS max_up,
                AVG(temp_cpu_master) AS avg_temp, MAX(temp_cpu_master) AS max_temp,
                AVG(active_hosts) AS avg_hosts
            FROM metrics WHERE ts >= ?
            GROUP BY DATE(ts, 'unixepoch', 'localtime')
            ORDER BY day DESC
        """, (since,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["uptime_pct"] = round(d["up_cnt"] / d["samples"] * 100, 1) if d["samples"] > 0 else 0
        result.append(d)
    return result


def open_outage(ts: int, cause: str = "connexion perdue") -> int:
    with _conn() as c:
        existing = c.execute("SELECT id FROM outages WHERE ended_at IS NULL").fetchone()
        if existing:
            return existing["id"]
        c.execute("INSERT INTO outages(started_at, cause) VALUES(?,?)", (ts, cause))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def close_outage(ts: int):
    with _conn() as c:
        row = c.execute("SELECT id, started_at FROM outages WHERE ended_at IS NULL").fetchone()
        if row:
            dur = ts - row["started_at"]
            c.execute(
                "UPDATE outages SET ended_at=?, duration_s=? WHERE id=?",
                (ts, dur, row["id"])
            )
            return dur
    return None


def get_outages(limit: int = 50, offset: int = 0) -> list:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM outages ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["duration_fmt"]  = _fmt_dur(d.get("duration_s"))
        d["started_fmt"]   = _ts_fmt(d.get("started_at"))
        d["ended_fmt"]     = _ts_fmt(d.get("ended_at")) if d.get("ended_at") else "En cours"
        result.append(d)
    return result


def get_config(key: str, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(key: str, value):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO config(key, value) VALUES(?,?)", (key, str(value)))


def get_all_config() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM config").fetchall()
    return {r["key"]: r["value"] for r in rows}


def create_user(username: str, password_hash: str):
    ts = int(datetime.now().timestamp())
    with _conn() as c:
        c.execute("INSERT INTO users(username, password, created_at) VALUES(?,?,?)",
                  (username, password_hash, ts))


def get_user(username: str):
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row else None


def update_password(username: str, new_hash: str):
    with _conn() as c:
        c.execute("UPDATE users SET password=? WHERE username=?", (new_hash, username))


def user_count() -> int:
    with _conn() as c:
        row = c.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
    return row["cnt"] if row else 0


def seed_config(defaults: dict):
    """Set keys only if they don't already exist."""
    for key, value in defaults.items():
        if get_config(key) is None:
            set_config(key, value)


def prune_metrics(keep_days: int = 365) -> int:
    cutoff = int((datetime.now() - timedelta(days=keep_days)).timestamp())
    with _conn() as c:
        c.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        return c.execute("SELECT changes()").fetchone()[0]


def _fmt_dur(secs) -> str:
    if not secs:
        return "—"
    secs = int(secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}j {h}h{m:02d}m"
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _ts_fmt(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts)).strftime("%d/%m/%Y %H:%M:%S")
