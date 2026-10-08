"""
db.py — Couche SQLite pour Freebox Monitor.
"""
import sqlite3
import os
import time
import logging
import threading
from datetime import datetime, timedelta
from calendar import monthrange
from contextlib import contextmanager
import crypto
from format_utils import fmt_dur as _fmt_dur

log = logging.getLogger(__name__)

ENCRYPTED_KEYS = {"smtp_password", "github_token"}

# Pourquoi les agrégations enveloppent certaines colonnes dans NULLIF(x, 0).
#
# insert_metric écrit désormais NULL (et non 0) quand une valeur est inconnue,
# et AVG/MIN/MAX ignorent nativement les NULL. Mais les lignes déjà en base
# contiennent des sentinelles 0 héritées, et la rétention est de 365 jours :
# sans filtrage en lecture, les statistiques resteraient fausses pendant un an.
# Symptôme principal : MIN(bytes_down) valait 0 sur toute période contenant une
# coupure, donc MAX-MIN renvoyait le compteur de vie complet de la box (mesuré :
# ~11 To affichés au lieu de ~2,3 To réels sur 30 jours).
#
# NULLIF est appliqué en LECTURE SEULE — la base n'est pas réécrite — et
# uniquement là où 0 ne peut pas être une mesure valide :
#   - bytes_down / bytes_up : compteurs cumulatifs, jamais remis à 0 hors reboot ;
#   - temp_* : aucune box allumée ne mesure 0 °C (c'est déjà ainsi que _first()
#     interprétait un 0 avant de l'écrire).
# Volontairement EXCLUS du filtrage, car 0 y est une mesure légitime :
#   - active_hosts : 0 hôte joignable est un résultat valide, et rien ne permet
#     de le distinguer a posteriori d'un échec de collecte historique ;
#   - rate_down / rate_up : 0 b/s = simplement aucun trafic.

# Poids temporel maximum accordé à un échantillon dans le calcul de disponibilité
# pondérée par le temps (voir _UPTIME_CTE). Si le service de monitoring a été
# arrêté pendant des heures, l'écart entre deux échantillons consécutifs ne
# représente pas un état "observé" : on plafonne sa contribution pour qu'un trou
# d'observation n'écrase pas la statistique de la période.
UPTIME_MAX_GAP_S = 300

# Sérialise tous les accès DB entre threads.
# RLock (réentrant) : un même thread peut ouvrir plusieurs _conn() imbriqués
# (ex: prune_metrics → prune_rate_limits) sans deadlock.
#
# Toujours nécessaire malgré le mode WAL (voir init_db) : WAL permet à SQLite de
# gérer nativement des lectures concurrentes pendant une écriture, ce qui suffirait
# pour de simples requêtes indépendantes. Mais plusieurs fonctions ici (ex:
# open_outage, is_rate_limited_db) exécutent un SELECT puis un INSERT/UPDATE comme
# une unité logique "lire-puis-décider-puis-écrire", et WAL seul ne protège pas
# contre une race applicative où deux threads liraient le même état avant que l'un
# des deux écrive (ex: deux incidents ouverts en même temps, double décompte de
# rate limit). Le lock garantit l'atomicité de ces séquences. On pourrait
# envisager de le remplacer par un verrou plus fin (ex: lock dédié par table, ou
# transactions SQLite explicites BEGIN IMMEDIATE) pour réduire la contention entre
# l'écriture de la boucle de collecte et les lectures API (/api/stats), mais ce
# n'est pas fait ici par prudence — à valider avec des tests de charge avant de
# retirer ou d'affaiblir ce verrou.
_db_lock = threading.RLock()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "freebox.db")


def _migrate(c):
    """Migrations incrémentales sur la DB existante."""
    cols_users = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
    if "recovery_email" not in cols_users:
        c.execute("ALTER TABLE users ADD COLUMN recovery_email TEXT DEFAULT ''")
    cols_out = [r[1] for r in c.execute("PRAGMA table_info(outages)").fetchall()]
    if "is_test" not in cols_out:
        c.execute("ALTER TABLE outages ADD COLUMN is_test INTEGER DEFAULT 0")
    if "note" not in cols_out:
        c.execute("ALTER TABLE outages ADD COLUMN note TEXT DEFAULT ''")
    if "external_ip" not in cols_out:
        c.execute("ALTER TABLE outages ADD COLUMN external_ip TEXT DEFAULT ''")
    if "flap_count" not in cols_out:
        c.execute("ALTER TABLE outages ADD COLUMN flap_count INTEGER DEFAULT 0")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # WAL mode : lectures et écriture peuvent se faire simultanément.
    # Doit être activé hors transaction, via une connexion dédiée.
    _wal = sqlite3.connect(DB_PATH)
    _wal.execute("PRAGMA journal_mode=WAL")
    _wal.execute("PRAGMA synchronous=NORMAL")
    _wal.close()
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
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                username       TEXT UNIQUE NOT NULL,
                password       TEXT NOT NULL,
                created_at     INTEGER NOT NULL,
                recovery_email TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS reset_codes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT NOT NULL,
                code       TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used       INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS rate_limits (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ip      TEXT    NOT NULL,
                action  TEXT    NOT NULL,
                ts      INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ping_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         INTEGER NOT NULL,
                host       TEXT    NOT NULL,
                latency_ms REAL,
                lost       INTEGER DEFAULT 0
            );
            -- Échecs de l'appel à l'API locale de la box qui NE sont PAS des
            -- coupures réseau : session expirée, HTTP 4xx/5xx, JSON invalide,
            -- résolution DNS ou TCP ponctuellement en échec alors que la box
            -- répond au ping. Tracés ici au lieu de polluer `outages`.
            CREATE TABLE IF NOT EXISTS api_errors (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         INTEGER NOT NULL,
                kind       TEXT    NOT NULL,   -- dns | timeout | connexion | session | http | reponse
                outcome    TEXT    NOT NULL,   -- resolu_au_retry | reseau_ok | coupure_confirmee
                detail     TEXT    DEFAULT '',
                box_ping_ok   INTEGER,         -- 1/0/NULL : la box répond-elle au ping ICMP ?
                box_latency_ms REAL,
                net_ping_ok   INTEGER          -- 1/0/NULL : cible externe (ping_target) joignable ?
            );
            CREATE INDEX IF NOT EXISTS idx_api_errors_ts ON api_errors(ts);
            CREATE INDEX IF NOT EXISTS idx_metrics_ts    ON metrics(ts);
            CREATE INDEX IF NOT EXISTS idx_outages_start ON outages(started_at);
            CREATE INDEX IF NOT EXISTS idx_rate_limits   ON rate_limits(action, ip, ts);
            CREATE INDEX IF NOT EXISTS idx_ping_log_ts   ON ping_log(ts);
        """)
        _migrate(c)


@contextmanager
def _conn():
    with _db_lock:
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
        c.row_factory = sqlite3.Row
        # journal_mode=WAL est persistant (propriété du fichier .db, réglée une
        # fois dans init_db), mais synchronous est un réglage par connexion : il
        # doit être réappliqué à chaque ouverture pour rester en mode NORMAL
        # (sinon SQLite retombe sur FULL par défaut, plus lent).
        c.execute("PRAGMA synchronous=NORMAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()


# Aliases par ordre de priorité pour chaque capteur logique.
# Couvre : Delta/Revolution (cp_master/ap/slave), Ultra (cpua/cpub), et génériques.
_S_CPU_MAIN  = ["temp_cpu_cp_master", "temp_cpua", "temp_cpu",  "temp_cpu1", "temp_t1"]
_S_CPU_AP    = ["temp_cpu_ap",        "temp_cpub", "temp_cpu2"]
_S_CPU_SLAVE = ["temp_cpu_cp_slave",  "temp_cpuc", "temp_cpu3"]
_S_HDD0      = ["temp_hdd0",          "temp_hdd",  "temp_disk"]
_S_T1        = ["temp_t1",  "temp_sw",  "temp_pcie"]
_S_T2        = ["temp_t2",  "temp_nb"]
_S_T3        = ["temp_t3"]
_F_FAN0      = ["fan0_speed", "fan_speed", "fan0"]
_F_FAN1      = ["fan1_speed", "fan1"]


def _first(d: dict, keys: list, default=None):
    """Retourne la première valeur renseignée parmi les clés candidates.

    Un capteur absent / en erreur donne `default` (None par défaut = « valeur
    inconnue »), ce qui est écrit NULL en base et donc ignoré par AVG/MIN/MAX.
    Écrire 0 à la place faussait les moyennes de température (un capteur
    manquant tirait AVG(temp_cpu_master) vers le bas).

    Attention : une valeur de 0 est traitée ici comme « non renseignée » car
    aucun capteur de température ni ventilateur de la Freebox ne remonte
    légitimement 0 (0 °C / 0 RPM sur une box allumée = absence de capteur).
    Ce raccourci ne s'applique PAS aux compteurs métier (hôtes LAN, débits),
    gérés séparément via _measured().
    """
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return default


def _measured(d: dict, key: str, default=None):
    """Valeur mesurée d'un compteur, ou `default` (None → NULL) si indisponible.

    Contrairement à _first(), un 0 explicitement remonté par la box est une
    mesure valide (0 octet transféré, 0 b/s, 0 hôte joignable) et est conservé
    tel quel. Seule l'absence de la clé — c'est-à-dire un collecteur qui a levé
    une exception — donne NULL.
    """
    v = d.get(key, default)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def insert_metric(data: dict):
    m = data
    conn = m.get("connection", {})
    sys  = m.get("system", {})
    lan  = m.get("lan", {})
    sens = sys.get("sensors", {})
    fans = sys.get("fans", {})
    # active_hosts vaut "?" quand collect_lan a échoué → NULL (inconnu).
    # Un vrai 0 (aucun hôte joignable) reste 0 : c'est une mesure valide.
    hosts = _measured(lan, "active_hosts")
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
            # Pendant une coupure, collect_connection() lève et ces clés sont
            # absentes : on écrit NULL (valeur inconnue) et non 0. Un 0 écrit ici
            # cassait MAX(bytes_down)-MIN(bytes_down) (le MIN tombait à 0 et le
            # delta devenait le compteur de vie complet de la box).
            _measured(conn, "rate_down"),      _measured(conn, "rate_up"),
            _measured(conn, "bandwidth_down"), _measured(conn, "bandwidth_up"),
            _measured(conn, "bytes_down"),     _measured(conn, "bytes_up"),
            _first(sens, _S_HDD0),
            _first(sens, _S_T1),         _first(sens, _S_T2),
            _first(sens, _S_T3),
            _first(sens, _S_CPU_MAIN),
            _first(sens, _S_CPU_AP),
            _first(sens, _S_CPU_SLAVE),
            _first(fans, _F_FAN0),       _first(fans, _F_FAN1),
            hosts,
            _measured(sys, "uptime_val"),
        ))


def get_history(seconds: int = 600) -> list:
    since = int((datetime.now() - timedelta(seconds=seconds)).timestamp())
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM metrics WHERE ts >= ? ORDER BY ts ASC", (since,)
        ).fetchall()
    return [dict(r) for r in rows]


# Pondération temporelle de la disponibilité.
#
# Historiquement uptime_pct = up_samples / samples, ce qui suppose que chaque
# échantillon pèse la même durée. Or l'intervalle de collecte a varié (10 à 55 s
# selon la durée du cycle, avant que la boucle ne vise un intervalle fixe), et un
# cycle pendant une coupure est justement plus lent (timeouts HTTP) : les
# échantillons "down" étaient donc sous-pondérés et la disponibilité surestimée.
#
# On pondère chaque échantillon par l'écart réel jusqu'au suivant (plafonné à
# UPTIME_MAX_GAP_S, cf. constante). Le biais se réduit mécaniquement sur les
# données collectées depuis le passage à un intervalle fixe, mais le calcul
# pondéré reste nécessaire pour l'historique déjà en base, qui n'est pas
# réécrit.
_UPTIME_WEIGHT_SQL = """
    SELECT
        ts, conn_state,
        MIN(COALESCE(LEAD(ts) OVER (ORDER BY ts), ts) - ts, {cap}) AS w
    FROM metrics WHERE {where}
"""


def _uptime_pct(c, where: str, params: tuple, samples: int, up_samples: int):
    """Disponibilité en % pondérée par le temps réel entre échantillons.

    Retombe sur le ratio d'échantillons si la fenêtre ne contient pas assez de
    points pour mesurer une durée (un seul échantillon → poids total nul)."""
    sql = _UPTIME_WEIGHT_SQL.format(cap=UPTIME_MAX_GAP_S, where=where)
    row = c.execute(f"""
        WITH w AS ({sql})
        SELECT
            COALESCE(SUM(w), 0)                                       AS total_s,
            COALESCE(SUM(CASE WHEN conn_state='up' THEN w ELSE 0 END), 0) AS up_s
        FROM w
    """, params).fetchone()
    total_s = (row["total_s"] or 0) if row else 0
    if total_s > 0:
        return round((row["up_s"] or 0) / total_s * 100, 2)
    if samples > 0:
        return round(up_samples / samples * 100, 2)
    return None


def get_period_stats(since_ts: int, until_ts: int = None) -> dict:
    metric_where = "ts >= ?" + (" AND ts < ?" if until_ts is not None else "")
    outage_where = "started_at >= ?" + (" AND started_at < ?" if until_ts is not None else "")
    params = (since_ts, until_ts) if until_ts is not None else (since_ts,)
    with _conn() as c:
        # Voir la note sur les sentinelles 0 en tête de module. Ne pas remettre
        # de COALESCE(..., 0) sur ces colonnes : MIN(bytes_down) repasserait à 0.
        row = c.execute(f"""
            SELECT
                COUNT(*)                                           AS samples,
                SUM(CASE WHEN conn_state='up' THEN 1 ELSE 0 END)  AS up_samples,
                AVG(rate_down)   AS avg_down,  MAX(rate_down) AS max_down,
                AVG(rate_up)     AS avg_up,    MAX(rate_up)   AS max_up,
                AVG(active_hosts) AS avg_hosts, MAX(active_hosts) AS max_hosts,
                MAX(bytes_down) - MIN(NULLIF(bytes_down, 0)) AS delta_bytes_down,
                MAX(bytes_up)   - MIN(NULLIF(bytes_up, 0))   AS delta_bytes_up,
                AVG(NULLIF(temp_cpu_master, 0)) AS avg_temp,
                MAX(NULLIF(temp_cpu_master, 0)) AS max_temp
            FROM metrics WHERE {metric_where}
        """, params).fetchone()
        out_row = c.execute(f"""
            SELECT
                SUM(CASE WHEN is_test=0 THEN 1 ELSE 0 END) AS cnt,
                SUM(CASE WHEN is_test=1 THEN 1 ELSE 0 END) AS test_cnt,
                COALESCE(SUM(CASE WHEN is_test=0 THEN duration_s ELSE 0 END), 0) AS total_s
            FROM outages
            WHERE {outage_where} AND ended_at IS NOT NULL
        """, params).fetchone()
        result = dict(row) if row else {}
        result["uptime_pct"] = _uptime_pct(
            c, metric_where, params,
            result.get("samples") or 0, result.get("up_samples") or 0,
        )
    result["outage_count"]      = out_row["cnt"]      if out_row else 0
    result["test_outage_count"] = out_row["test_cnt"] if out_row else 0
    result["outage_total_s"]    = out_row["total_s"]  if out_row else 0
    result["outage_total_fmt"]  = _fmt_dur(result.get("outage_total_s", 0))
    # Erreurs de l'API locale de la box, comptées à part des vraies coupures.
    api = get_api_error_summary(since_ts, until_ts)
    result["api_error_count"]       = api["total"]
    result["api_error_net_ok"]      = api["false_positives"]
    result["api_error_retry_ok"]    = api["retry_ok"]
    result["api_error_by_kind"]     = api["by_kind"]
    return result


def _uptime_pct_by_day(c, where: str, params: tuple) -> dict:
    """Disponibilité pondérée par le temps, par jour local. Voir _uptime_pct.

    LEAD() est calculé sur toute la fenêtre (et non par jour) pour ne pas perdre
    l'intervalle du dernier échantillon de chaque journée ; cet intervalle est
    attribué au jour de l'échantillon courant.
    """
    sql = _UPTIME_WEIGHT_SQL.format(cap=UPTIME_MAX_GAP_S, where=where)
    rows = c.execute(f"""
        WITH w AS ({sql})
        SELECT
            DATE(ts, 'unixepoch', 'localtime') AS day,
            COALESCE(SUM(w), 0)                AS total_s,
            COALESCE(SUM(CASE WHEN conn_state='up' THEN w ELSE 0 END), 0) AS up_s
        FROM w
        GROUP BY DATE(ts, 'unixepoch', 'localtime')
    """, params).fetchall()
    out = {}
    for r in rows:
        total_s = r["total_s"] or 0
        if total_s > 0:
            out[r["day"]] = round((r["up_s"] or 0) / total_s * 100, 1)
    return out


def get_daily_uptime(year: int, month: int) -> dict:
    _, days = monthrange(year, month)
    start_dt = datetime(year, month, 1)
    end_dt   = datetime(year, month, days, 23, 59, 59)
    start_ts = int(start_dt.timestamp())
    end_ts   = int(end_dt.timestamp())
    with _conn() as c:
        rows = c.execute("""
            SELECT
                DATE(ts, 'unixepoch', 'localtime')                AS day,
                COUNT(*)                                           AS total,
                SUM(CASE WHEN conn_state='up' THEN 1 ELSE 0 END)  AS up_cnt,
                AVG(rate_down)   AS avg_down,
                AVG(rate_up)     AS avg_up,
                MAX(rate_down)   AS max_down,
                AVG(NULLIF(temp_cpu_master, 0)) AS avg_temp
            FROM metrics
            WHERE ts >= ? AND ts <= ?
            GROUP BY DATE(ts, 'unixepoch', 'localtime')
            ORDER BY day
        """, (start_ts, end_ts)).fetchall()
        out_rows = c.execute("""
            SELECT
                DATE(started_at, 'unixepoch', 'localtime') AS day,
                SUM(CASE WHEN is_test=0 THEN 1 ELSE 0 END) AS real_cnt,
                SUM(CASE WHEN is_test=1 THEN 1 ELSE 0 END) AS test_cnt
            FROM outages
            WHERE started_at >= ? AND started_at <= ? AND ended_at IS NOT NULL
            GROUP BY DATE(started_at, 'unixepoch', 'localtime')
        """, (start_ts, end_ts)).fetchall()
        pct_by_day = _uptime_pct_by_day(c, "ts >= ? AND ts <= ?", (start_ts, end_ts))
    outage_by_day = {
        r["day"]: {"real_cnt": r["real_cnt"] or 0, "test_cnt": r["test_cnt"] or 0}
        for r in out_rows
    }
    result = {}
    for r in rows:
        d = dict(r)
        # Pondéré par le temps ; repli sur le ratio d'échantillons si la journée
        # ne contient pas assez de points pour mesurer une durée.
        fallback = round(d["up_cnt"] / d["total"] * 100, 1) if d["total"] > 0 else 0
        d["uptime_pct"] = pct_by_day.get(d["day"], fallback)
        o = outage_by_day.get(d["day"], {})
        d["real_outage_count"] = o.get("real_cnt", 0)
        d["test_outage_count"] = o.get("test_cnt", 0)
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
                AVG(NULLIF(temp_cpu_master, 0)) AS avg_temp,
                MAX(NULLIF(temp_cpu_master, 0)) AS max_temp,
                AVG(active_hosts) AS avg_hosts
            FROM metrics WHERE ts >= ?
            GROUP BY DATE(ts, 'unixepoch', 'localtime')
            ORDER BY day DESC
        """, (since,)).fetchall()
        pct_by_day = _uptime_pct_by_day(c, "ts >= ?", (since,))
    result = []
    for r in rows:
        d = dict(r)
        fallback = round(d["up_cnt"] / d["samples"] * 100, 1) if d["samples"] > 0 else 0
        d["uptime_pct"] = pct_by_day.get(d["day"], fallback)
        result.append(d)
    return result


def open_outage(ts: int, cause: str = "connexion perdue", is_test: int = 0,
                merge_window_s: int = 0) -> tuple:
    """Ouvre une coupure, ou la fusionne avec le dernier incident si la connexion
    vient de flapper (reconnexion trop brève pour être un vrai rétablissement).

    Retourne (outage_id, is_new) où is_new=False signifie soit que la coupure était
    déjà ouverte, soit que l'incident précédent a été rouvert/prolongé (fusion de flap) :
    dans les deux cas, il ne faut pas redéclencher les effets de bord d'une "nouvelle" coupure
    (alerte, capture IP externe).
    """
    with _conn() as c:
        existing = c.execute("SELECT id FROM outages WHERE ended_at IS NULL").fetchone()
        if existing:
            return existing["id"], False
        if merge_window_s > 0:
            last = c.execute(
                "SELECT id, ended_at FROM outages WHERE ended_at IS NOT NULL "
                "ORDER BY ended_at DESC LIMIT 1"
            ).fetchone()
            if last and last["ended_at"] is not None and (ts - last["ended_at"]) <= merge_window_s:
                # Flap : on rouvre/prolonge l'incident précédent au lieu d'en créer un nouveau.
                c.execute(
                    "UPDATE outages SET ended_at=NULL, duration_s=NULL, "
                    "flap_count=COALESCE(flap_count,0)+1 WHERE id=?",
                    (last["id"],)
                )
                return last["id"], False
        c.execute("INSERT INTO outages(started_at, cause, is_test) VALUES(?,?,?)", (ts, cause, is_test))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0], True


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


def get_outages(limit: int = 50, offset: int = 0) -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM outages").fetchone()[0]
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
        d["is_test"]       = int(d.get("is_test") or 0)
        d["note"]          = d.get("note") or ""
        d["flap_count"]    = int(d.get("flap_count") or 0)
        result.append(d)
    return {"items": result, "total": total}


def is_outage_open(outage_id: int) -> bool:
    """Indique si la coupure donnée est toujours ouverte."""
    with _conn() as c:
        row = c.execute(
            "SELECT ended_at FROM outages WHERE id=?", (outage_id,)
        ).fetchone()
    return row is not None and row["ended_at"] is None


def get_outages_between(start_ts: int, end_ts: int) -> list:
    """Retourne toutes les coupures ayant commencé dans l'intervalle demandé."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM outages WHERE started_at >= ? AND started_at < ? "
            "ORDER BY started_at DESC",
            (start_ts, end_ts),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["duration_fmt"] = _fmt_dur(item.get("duration_s"))
        item["started_fmt"] = _ts_fmt(item.get("started_at"))
        item["ended_fmt"] = _ts_fmt(item.get("ended_at")) if item.get("ended_at") else "En cours"
        item["flap_count"] = int(item.get("flap_count") or 0)
        result.append(item)
    return result


def get_config(key: str, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    return crypto.decrypt(row["value"]) if row["value"] else default


def get_config_raw(key: str, default=None):
    """Retourne la valeur telle que stockée en base, SANS déchiffrement.

    Utilisé pour détecter si un secret est déjà chiffré (préfixe 'enc:') sans
    passer par get_config(), qui déchiffre et renverrait donc toujours une
    valeur en clair (voir migration des secrets au démarrage dans monitor.py)."""
    with _conn() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if not row or not row["value"]:
        return default
    return row["value"]


def set_config(key: str, value):
    if key in ENCRYPTED_KEYS and value and not crypto.is_encrypted(str(value)):
        value = crypto.encrypt(str(value))
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO config(key, value) VALUES(?,?)", (key, str(value)))


def get_all_config() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM config").fetchall()
    result = {}
    for r in rows:
        key, val = r["key"], r["value"]
        result[key] = crypto.decrypt(val) if (key in ENCRYPTED_KEYS and val) else val
    return result


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


def set_recovery_email(username: str, email: str):
    with _conn() as c:
        c.execute("UPDATE users SET recovery_email=? WHERE username=?", (email.strip(), username))


def get_recovery_email(username: str) -> str:
    with _conn() as c:
        row = c.execute("SELECT recovery_email FROM users WHERE username=?", (username,)).fetchone()
    return (row["recovery_email"] or "") if row else ""


def check_recovery_email(username: str, email: str) -> bool:
    """Retourne True si l'email correspond à l'email de récupération enregistré."""
    stored = get_recovery_email(username)
    return stored and stored.lower() == email.strip().lower()


def create_reset_code(username: str) -> str:
    import secrets as _sec
    code = str(_sec.randbelow(900000) + 100000)  # 6 chiffres garanti
    expires_at = int(datetime.now().timestamp()) + 900  # 15 minutes
    with _conn() as c:
        # Invalider les anciens codes non utilisés pour ce user
        c.execute("UPDATE reset_codes SET used=1 WHERE username=? AND used=0", (username,))
        c.execute(
            "INSERT INTO reset_codes(username, code, expires_at, used) VALUES(?,?,?,0)",
            (username, code, expires_at)
        )
    return code


def verify_reset_code(username: str, code: str):
    """Retourne l'id du code si valide, None sinon."""
    now = int(datetime.now().timestamp())
    with _conn() as c:
        row = c.execute(
            "SELECT id FROM reset_codes WHERE username=? AND code=? AND used=0 AND expires_at>?",
            (username, code, now)
        ).fetchone()
    return row["id"] if row else None


def consume_reset_code(code_id: int):
    with _conn() as c:
        c.execute("UPDATE reset_codes SET used=1 WHERE id=?", (code_id,))


def mark_outage(outage_id: int, is_test=None, note: str = None):
    """Qualifier une coupure comme test/réelle et/ou modifier sa note.
    Passer is_test=None pour ne pas toucher au champ is_test."""
    with _conn() as c:
        if is_test is not None and note is not None:
            c.execute("UPDATE outages SET is_test=?, note=? WHERE id=?", (is_test, note, outage_id))
        elif is_test is not None:
            c.execute("UPDATE outages SET is_test=? WHERE id=?", (is_test, outage_id))
        elif note is not None:
            c.execute("UPDATE outages SET note=? WHERE id=?", (note, outage_id))


def reset_outages_by_days(date_list: list):
    """Marquer toutes les coupures des jours donnés comme is_test=1."""
    with _conn() as c:
        for date_str in date_list:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                day_start = int(dt.timestamp())
                day_end   = int((dt + timedelta(days=1)).timestamp()) - 1
                c.execute(
                    "UPDATE outages SET is_test=1 WHERE started_at >= ? AND started_at <= ?",
                    (day_start, day_end)
                )
            except (ValueError, TypeError) as e:
                log.warning(
                    "reset_outages_by_days: date ignorée %r (format attendu AAAA-MM-JJ) : %s",
                    date_str, e
                )


def seed_config(defaults: dict):
    """Set keys only if they don't already exist."""
    for key, value in defaults.items():
        if get_config(key) is None:
            set_config(key, value)


def is_rate_limited_db(ip: str, action: str, max_attempts: int, window_s: int) -> bool:
    """Vérifie si ip a dépassé max_attempts pour action dans les window_s dernières secondes.

    LECTURE SEULE : n'enregistre plus la tentative. L'appelant doit appeler
    record_failed_attempt() uniquement en cas d'ÉCHEC d'authentification.
    Auparavant, chaque POST (y compris les connexions réussies) consommait un
    jeton, si bien que 5 connexions légitimes successives bloquaient la 6ᵉ.
    """
    now = int(time.time())
    since = now - window_s
    with _conn() as c:
        count = c.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE ip=? AND action=? AND ts>=?",
            (ip, action, since)
        ).fetchone()[0]
    return count >= max_attempts


def record_failed_attempt(ip: str, action: str):
    """Comptabilise une tentative échouée pour (ip, action) dans le rate limiting."""
    with _conn() as c:
        c.execute(
            "INSERT INTO rate_limits(ip, action, ts) VALUES(?,?,?)",
            (ip, action, int(time.time()))
        )


def clear_rate_limit(ip: str, action: str):
    """Remet le compteur à zéro après un succès (connexion/réinitialisation réussie)."""
    with _conn() as c:
        c.execute("DELETE FROM rate_limits WHERE ip=? AND action=?", (ip, action))


def rate_limit_retry_after(ip: str, action: str, max_attempts: int, window_s: int) -> int:
    """Retourne les secondes restantes avant fin du blocage, ou 0 si non limité.
    Ne modifie pas la table (lecture seule)."""
    now = int(time.time())
    since = now - window_s
    with _conn() as c:
        count = c.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE ip=? AND action=? AND ts>=?",
            (ip, action, since)
        ).fetchone()[0]
        if count < max_attempts:
            return 0
        oldest = c.execute(
            "SELECT MIN(ts) FROM rate_limits WHERE ip=? AND action=? AND ts>=?",
            (ip, action, since)
        ).fetchone()[0]
    return max(0, oldest + window_s - now) if oldest else 0


def prune_rate_limits(max_age_s: int = 86400):
    """Supprime les entrées plus vieilles que max_age_s."""
    cutoff = int(time.time()) - max_age_s
    with _conn() as c:
        c.execute("DELETE FROM rate_limits WHERE ts<?", (cutoff,))


def prune_metrics(keep_days: int = 365) -> int:
    cutoff = int((datetime.now() - timedelta(days=keep_days)).timestamp())
    rate_cutoff = int(time.time()) - 86400
    with _conn() as c:
        c.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        c.execute("DELETE FROM rate_limits WHERE ts < ?", (rate_cutoff,))
        c.execute("DELETE FROM ping_log WHERE ts < ?", (cutoff,))
        c.execute("DELETE FROM api_errors WHERE ts < ?", (cutoff,))
        return c.execute("SELECT changes()").fetchone()[0]


def insert_ping_log(ts: int, host: str, latency_ms, lost: int):
    with _conn() as c:
        c.execute(
            "INSERT INTO ping_log(ts, host, latency_ms, lost) VALUES(?,?,?,?)",
            (ts, host, latency_ms, lost)
        )


def get_ping_history(seconds: int = 1800, host: str = None) -> list:
    """Historique de ping_log sur la fenêtre donnée. `host` filtre sur une
    cible précise (ex: 'box' pour le ping dédié routeur) ; sans filtre,
    retourne toutes les cibles confondues (comportement historique)."""
    since = int((datetime.now() - timedelta(seconds=seconds)).timestamp())
    with _conn() as c:
        if host:
            rows = c.execute(
                "SELECT ts, host, latency_ms, lost FROM ping_log "
                "WHERE ts >= ? AND host = ? ORDER BY ts ASC",
                (since, host)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT ts, host, latency_ms, lost FROM ping_log WHERE ts >= ? ORDER BY ts ASC",
                (since,)
            ).fetchall()
    return [dict(r) for r in rows]


def get_last_ping(max_age_s: int = 30) -> dict:
    """Dernier ping enregistré par `_bg_ping`, s'il est assez récent pour servir
    de corroboration indépendante (None sinon)."""
    since = int(time.time()) - max_age_s
    with _conn() as c:
        row = c.execute(
            "SELECT ts, host, latency_ms, lost FROM ping_log "
            "WHERE ts >= ? ORDER BY ts DESC LIMIT 1",
            (since,)
        ).fetchone()
    return dict(row) if row else None


def insert_api_error(ts: int, kind: str, outcome: str, detail: str = "",
                     box_ping_ok=None, box_latency_ms=None, net_ping_ok=None):
    """Trace un échec de l'appel à l'API locale de la box. Volontairement séparé
    de la table `outages` : ce n'est une coupure réseau que si outcome vaut
    'coupure_confirmee' (et dans ce cas une entrée `outages` existe aussi)."""
    with _conn() as c:
        c.execute(
            "INSERT INTO api_errors(ts, kind, outcome, detail, box_ping_ok, "
            "box_latency_ms, net_ping_ok) VALUES(?,?,?,?,?,?,?)",
            (ts, kind, outcome, (detail or "")[:500],
             box_ping_ok, box_latency_ms, net_ping_ok)
        )


def get_api_errors(limit: int = 50, offset: int = 0) -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM api_errors").fetchone()[0]
        rows = c.execute(
            "SELECT * FROM api_errors ORDER BY ts DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["ts_fmt"] = _ts_fmt(d.get("ts"))
        items.append(d)
    return {"items": items, "total": total}


def get_api_error_summary(since_ts: int, until_ts: int = None) -> dict:
    """Décompte des erreurs d'API locale sur une période, par issue.

    `false_positives` = ce que les versions précédentes comptabilisaient à tort
    comme des coupures réseau (la box répondait au ping)."""
    where = "ts >= ?" + (" AND ts < ?" if until_ts is not None else "")
    params = (since_ts, until_ts) if until_ts is not None else (since_ts,)
    with _conn() as c:
        rows = c.execute(
            f"SELECT outcome, COUNT(*) AS n FROM api_errors WHERE {where} GROUP BY outcome",
            params
        ).fetchall()
        kinds = c.execute(
            f"SELECT kind, COUNT(*) AS n FROM api_errors WHERE {where} GROUP BY kind ORDER BY n DESC",
            params
        ).fetchall()
    by_outcome = {r["outcome"]: r["n"] for r in rows}
    return {
        "total":           sum(by_outcome.values()),
        "retry_ok":        by_outcome.get("resolu_au_retry", 0),
        "false_positives": by_outcome.get("reseau_ok", 0),
        "confirmed":       by_outcome.get("coupure_confirmee", 0),
        "by_kind":         {r["kind"]: r["n"] for r in kinds},
    }


def set_outage_external_ip(outage_id: int, ip: str):
    with _conn() as c:
        c.execute("UPDATE outages SET external_ip=? WHERE id=?", (ip, outage_id))


# _fmt_dur est désormais importé depuis format_utils (voir en-tête du fichier) —
# conservé sous ce nom pour ne pas changer tous les appels existants.


def _ts_fmt(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts)).strftime("%d/%m/%Y %H:%M:%S")
