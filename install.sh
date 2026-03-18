#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
# Freebox Monitor — Script d'installation
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="freebox-monitor"

echo "══════════════════════════════════════════"
echo "  📡 Installation de Freebox Monitor"
echo "══════════════════════════════════════════"
echo ""

# 0. Choix du port
read -rp "  Port d'écoute [8000] : " PORT_INPUT
PORT="${PORT_INPUT:-8000}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
    echo "❌ Port invalide. Utilisez un entier entre 1 et 65535."
    exit 1
fi
echo "✓ Port sélectionné : $PORT"
echo ""

# 1. Vérifier Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 est requis. Installez-le avec: sudo apt install python3 python3-venv"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓ Python $PYTHON_VERSION détecté"

# 2. Créer le venv
echo ""
echo "→ Création de l'environnement virtuel..."
python3 -m venv "$SCRIPT_DIR/venv"
source "$SCRIPT_DIR/venv/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "✓ Dépendances installées"

# Écrire le port choisi dans config.json
"$SCRIPT_DIR/venv/bin/python3" - <<PYEOF
import json, os
cfg_path = os.path.join("$SCRIPT_DIR", "config.json")
with open(cfg_path) as f:
    cfg = json.load(f)
cfg["port"] = "$PORT"
with open(cfg_path, "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF
echo "✓ Port $PORT enregistré dans la configuration"

# 3. Générer le certificat SSL auto-signé
echo ""
echo "→ Génération du certificat SSL..."
mkdir -p "$SCRIPT_DIR/certs"
if [ ! -f "$SCRIPT_DIR/certs/cert.pem" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$SCRIPT_DIR/certs/key.pem" \
        -out "$SCRIPT_DIR/certs/cert.pem" \
        -days 3650 \
        -subj "/CN=freebox-monitor/O=Freebox Monitor/C=FR" \
        2>/dev/null
    echo "✓ Certificat SSL généré (valide 10 ans)"
else
    echo "✓ Certificat SSL existant conservé"
fi

# 4. Autorisation Freebox (interactif)
echo ""
if [ ! -f "$SCRIPT_DIR/credentials.json" ]; then
    echo "→ Autorisation auprès de la Freebox..."
    echo "  ⚠ Vous devrez valider sur l'écran LCD de la Freebox"
    echo ""
    cd "$SCRIPT_DIR"
    "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/auth.py"
    echo ""
    echo "✓ Autorisation obtenue"
else
    echo "✓ credentials.json existant conservé"
fi

# 5. Créer le service systemd
echo ""
echo "→ Installation du service systemd..."

cat > /tmp/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Freebox Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/monitor.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

mv /tmp/${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.service
echo "✓ Service systemd installé et activé"

# 6. Démarrer le service
echo ""
echo "→ Démarrage du service..."
systemctl start ${SERVICE_NAME}.service
sleep 2

if systemctl is-active --quiet ${SERVICE_NAME}.service; then
    echo "✓ Service démarré avec succès"
else
    echo "⚠ Le service n'a pas démarré correctement. Vérifiez avec:"
    echo "  sudo journalctl -u ${SERVICE_NAME} -f"
fi

# 7. Afficher l'URL d'accès
echo ""
IP=$(hostname -I | awk '{print $1}')
echo "══════════════════════════════════════════"
echo "  ✅ Installation terminée !"
echo ""
echo "  Accès : https://${IP}:${PORT}"
echo ""
echo "  Commandes utiles :"
echo "    sudo systemctl status ${SERVICE_NAME}"
echo "    sudo systemctl restart ${SERVICE_NAME}"
echo "    sudo journalctl -u ${SERVICE_NAME} -f"
echo "══════════════════════════════════════════"
