#!/bin/bash
set -e

# ── Certificat SSL auto-signé (généré une seule fois) ──────────────────────
if [ ! -f /app/certs/cert.pem ] || [ ! -f /app/certs/key.pem ]; then
  echo "[freebox-monitor] Génération du certificat SSL auto-signé..."
  mkdir -p /app/certs
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout /app/certs/key.pem \
    -out  /app/certs/cert.pem \
    -days 3650 \
    -subj "/CN=freebox-monitor/O=Freebox Monitor/C=FR" 2>/dev/null
  chmod 600 /app/certs/key.pem /app/certs/cert.pem
  echo "[freebox-monitor] Certificat généré."
fi

# ── Vérification credentials Freebox ───────────────────────────────────────
if [ ! -f /app/credentials.json ]; then
  echo ""
  echo "╔══════════════════════════════════════════════════════════════╗"
  echo "║  AUTORISATION FREEBOX REQUISE                                ║"
  echo "║                                                              ║"
  echo "║  Lancez cette commande UNE SEULE FOIS depuis votre réseau    ║"
  echo "║  local (là où votre Freebox est accessible) :                ║"
  echo "║                                                              ║"
  echo "║    docker compose run --rm -it freebox-monitor auth          ║"
  echo "║                                                              ║"
  echo "║  Puis validez sur l'écran LCD de votre Freebox.              ║"
  echo "║  Relancez ensuite : docker compose up -d                     ║"
  echo "╚══════════════════════════════════════════════════════════════╝"
  echo ""
  exit 1
fi

exec python3 monitor.py
