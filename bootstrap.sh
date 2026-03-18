#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
# Freebox Monitor — Bootstrap (installation one-liner)
# Usage : bash <(curl -fsSL https://raw.githubusercontent.com/YakuMawi/freebox-monitor/main/bootstrap.sh)
# ─────────────────────────────────────────────

REPO_URL="https://github.com/YakuMawi/freebox-monitor.git"
INSTALL_DIR="/root/freebox-monitor"

echo "══════════════════════════════════════════"
echo "  📡 Freebox Monitor — Bootstrap"
echo "══════════════════════════════════════════"
echo ""

# 1. Installer git si absent
if ! command -v git &>/dev/null; then
    if ! command -v apt-get &>/dev/null; then
        echo "❌ apt-get introuvable. Installez git manuellement puis relancez."
        exit 1
    fi
    echo "→ Installation de git..."
    apt-get update -qq
    apt-get install -y -qq git
    echo "✓ git installé"
    echo ""
fi

# 2. Cloner ou mettre à jour le dépôt
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "→ Dépôt existant détecté, mise à jour..."
    git -C "$INSTALL_DIR" pull --ff-only
    echo "✓ Dépôt mis à jour"
else
    echo "→ Clonage du dépôt dans $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    echo "✓ Dépôt cloné"
fi

echo ""

# 3. Lancer le script d'installation
cd "$INSTALL_DIR"
bash install.sh
