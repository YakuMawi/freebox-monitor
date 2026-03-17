# 📡 Freebox Monitor

Dashboard de monitoring temps réel pour Freebox (Delta, Pop, Ultra...) avec alertes, historique et rapports.

## Fonctionnalités

- **Dashboard temps réel** — Débit, températures, ventilateurs, hôtes LAN, switch, stockage
- **Graphiques** — Historique du débit et des températures avec Chart.js
- **Alertes SMTP** — Notifications par email en cas de coupure/rétablissement
- **Historique** — Base SQLite avec rétention 1 an, statistiques par période
- **Rapports mensuels** — Calendrier de disponibilité, export PDF
- **Authentification** — Compte admin protégé par mot de passe
- **SSL/HTTPS** — Certificat auto-signé sur le port 8000
- **Mise à jour** — Vérification et application depuis GitHub

## Prérequis

- Python 3.10+
- Réseau local avec accès à la Freebox (http://mafreebox.freebox.fr)
- OpenSSL (pour le certificat SSL)

## Installation rapide

```bash
git clone https://github.com/<votre-user>/freebox-monitor.git
cd freebox-monitor
sudo bash install.sh
```

Le script d'installation :
1. Crée un environnement virtuel Python
2. Installe les dépendances
3. Génère un certificat SSL auto-signé
4. Lance l'autorisation Freebox (validation sur l'écran LCD)
5. Crée et démarre le service systemd
6. Affiche l'URL d'accès

## Installation manuelle

```bash
# Cloner le dépôt
git clone https://github.com/<votre-user>/freebox-monitor.git
cd freebox-monitor

# Environnement virtuel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Certificat SSL (optionnel mais recommandé)
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout certs/key.pem -out certs/cert.pem \
    -days 3650 -subj "/CN=freebox-monitor"

# Autorisation Freebox
python3 auth.py

# Lancement
python3 monitor.py
```

## Configuration

### Premier compte

Au premier lancement, accédez à `https://<ip>:8000` — vous serez redirigé vers la page de création du compte administrateur.

### SMTP (alertes email)

Depuis le dashboard, cliquez sur **Paramètres** → onglet **Alertes SMTP**.

Configurez votre serveur SMTP (Gmail, OVH, etc.) et activez les alertes pour recevoir des notifications en cas de coupure.

### Mise à jour depuis GitHub

Depuis **Paramètres** → onglet **Mise à jour**, renseignez le dépôt GitHub (`owner/repo`) et vérifiez les nouvelles versions.

## Structure

```
freebox-monitor/
├── monitor.py          # Serveur Flask + collecte de données
├── db.py               # Couche SQLite
├── alerts.py           # Alertes SMTP
├── updater.py          # Mise à jour GitHub
├── auth.py             # Autorisation Freebox
├── config.json         # Configuration par défaut
├── requirements.txt    # Dépendances Python
├── VERSION             # Version courante
├── install.sh          # Script d'installation
├── templates/
│   ├── index.html      # Dashboard principal
│   ├── settings.html   # Page paramètres
│   ├── login.html      # Page de connexion
│   └── setup.html      # Configuration initiale
├── data/               # Base SQLite (gitignored)
├── certs/              # Certificats SSL (gitignored)
└── credentials.json    # Token Freebox (gitignored)
```

## Licence

MIT
