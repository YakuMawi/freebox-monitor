"""
format_utils.py — Formatage partagé de métriques (débits, octets, durées).

Implémentation unique utilisée par monitor.py, db.py et alerts.py — évite
la triple réimplémentation (fmt_bytes/fmt_gb dans monitor.py, _fmt_dur dans
db.py) repérée en revue de code.
"""


def fmt_bytes(bps) -> str:
    """Formate un débit en octets/s vers une chaîne lisible en bit/s (Kbit/Mbit/Gbit)."""
    bits = bps * 8
    if bits >= 1_000_000_000:
        return f"{bits / 1_000_000_000:.1f} Gbit/s"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f} Mbit/s"
    if bits >= 1_000:
        return f"{bits / 1_000:.0f} Kbit/s"
    return f"{bits} bit/s"


def fmt_gb(b) -> str:
    """Formate une quantité d'octets vers une chaîne lisible (Ko/Mo/Go/To)."""
    if b is None:
        return "—"
    if b >= 1e12:
        return f"{b/1e12:.2f} To"
    if b >= 1e9:
        return f"{b/1e9:.1f} Go"
    if b >= 1e6:
        return f"{b/1e6:.0f} Mo"
    return f"{b/1e3:.0f} Ko"


def fmt_dur(secs) -> str:
    """Formate une durée en secondes vers une chaîne lisible (j/h/m/s)."""
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
