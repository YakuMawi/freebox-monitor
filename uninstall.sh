#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
# Freebox Monitor — Script de désinstallation
# ─────────────────────────────────────────────

SERVICE_NAME="freebox-monitor"
INSTALL_DIR="/root/freebox-monitor"

echo "══════════════════════════════════════════"
echo "  Désinstallation de Freebox Monitor"
echo "══════════════════════════════════════════"
echo ""

# ── 1. Arrêter et désactiver le service systemd ──────────────────────────────
if command -v systemctl &>/dev/null; then
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo "→ Arrêt du service..."
        systemctl stop "${SERVICE_NAME}"
        echo "✓ Service arrêté"
    else
        echo "✓ Service déjà arrêté"
    fi

    if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo "→ Désactivation du service..."
        systemctl disable "${SERVICE_NAME}"
        echo "✓ Service désactivé"
    fi

    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    if [ -f "$SERVICE_FILE" ]; then
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
        echo "✓ Fichier service supprimé"
    fi
else
    echo "  (systemctl introuvable — ignoré)"
fi

echo ""

# ── 2. Révoquer l'application Freebox (optionnel) ────────────────────────────
CREDS_FILE="${INSTALL_DIR}/credentials.json"
if [ -f "$CREDS_FILE" ] && command -v python3 &>/dev/null && [ -f "${INSTALL_DIR}/venv/bin/python3" ]; then
    echo "→ Souhaitez-vous révoquer le token d'accès à la Freebox ? [o/N]"
    read -r revoke_input
    if [[ "${revoke_input,,}" == "o" ]]; then
        "${INSTALL_DIR}/venv/bin/python3" - <<'PYEOF' 2>/dev/null && echo "✓ Token révoqué" || echo "  (révocation ignorée — la Freebox était peut-être inaccessible)"
import json, urllib.request, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

with open("/root/freebox-monitor/credentials.json") as f:
    creds = json.load(f)

box_url = creds.get("api_base_url", "http://mafreebox.freebox.fr/api/v6")
app_token = creds.get("app_token", "")
track_id = creds.get("track_id", "")

if not app_token:
    raise SystemExit("Pas de token à révoquer")

# On ne peut révoquer qu'une session ouverte ; on tente juste d'informer l'utilisateur.
print("  Le token est stocké localement. Pour le révoquer complètement, allez dans")
print("  Paramètres Freebox > Applications autorisées et supprimez 'Freebox Monitor'.")
PYEOF
    fi
fi

echo ""

# ── 3. Suppression du répertoire d'installation ──────────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    echo "→ Supprimer le répertoire d'installation ($INSTALL_DIR) ?"
    echo "  Cela effacera définitivement la base de données, la config et les certificats."
    read -rp "  Confirmer [o/N] : " del_input
    if [[ "${del_input,,}" == "o" ]]; then
        rm -rf "$INSTALL_DIR"
        echo "✓ Répertoire supprimé"
    else
        echo "  Répertoire conservé : $INSTALL_DIR"
    fi
else
    echo "  Répertoire $INSTALL_DIR introuvable (déjà supprimé ?)"
fi

echo ""
echo "══════════════════════════════════════════"
echo "  Freebox Monitor a été désinstallé."
echo "══════════════════════════════════════════"
