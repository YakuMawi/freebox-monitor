# Freebox Monitor

Dashboard de monitoring temps réel pour Freebox (Delta, Pop, Ultra...) avec alertes, historique et rapports.

## Fonctionnalités

- **Dashboard temps réel** — Débit, températures, ventilateurs, hôtes LAN, switch, stockage
- **Graphiques** — Historique du débit et des températures avec Chart.js
- **Alertes SMTP** — Notifications par email en cas de coupure/rétablissement
- **Historique** — Base SQLite avec rétention 1 an, statistiques par période
- **Rapports mensuels** — Calendrier de disponibilité, export PDF
- **Authentification** — Compte admin protégé par mot de passe (Werkzeug pbkdf2)
- **Récupération de compte** — Réinitialisation du mot de passe par email (code OTP 6 chiffres, 15 min)
- **SSL/HTTPS** — Certificat auto-signé sur le port 8000
- **Mise à jour** — Vérification et application depuis GitHub via l'interface web

## Prérequis

- Python 3.10+
- Linux avec systemd
- Réseau local avec accès à la Freebox (http://mafreebox.freebox.fr)
- OpenSSL (pour le certificat SSL)

## Installation en une commande

La méthode la plus simple. Requiert uniquement `curl` et un accès root :

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YakuMawi/freebox-monitor/main/bootstrap.sh)
```

Le bootstrap installe automatiquement `git` si absent, clone le dépôt dans `/root/freebox-monitor`, puis lance `install.sh`.

## Installation classique (git clone)

```bash
git clone https://github.com/YakuMawi/freebox-monitor.git
cd freebox-monitor
bash install.sh
```

Le script d'installation :
1. Installe les dépendances système manquantes (`python3`, `python3-venv`, `openssl`) via `apt-get`
2. Demande le port d'écoute (défaut : `8000`)
3. Crée un environnement virtuel Python et installe les dépendances Python
4. Génère un certificat SSL auto-signé (10 ans)
5. Lance l'autorisation Freebox (validation sur l'écran LCD)
6. Crée et démarre le service systemd
7. Affiche l'URL d'accès

## Installation manuelle

```bash
git clone https://github.com/YakuMawi/freebox-monitor.git
cd freebox-monitor

# Environnement virtuel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Certificat SSL
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

Au premier lancement, accédez à `https://<ip>:8000` — vous serez redirigé vers la page de création du compte administrateur. Renseignez un **email de récupération** pour pouvoir réinitialiser votre mot de passe en cas d'oubli.

### SMTP (alertes email)

Depuis le dashboard, cliquez sur **Paramètres** → onglet **Alertes SMTP**.

Configurez votre serveur SMTP (Gmail, OVH, etc.) et activez les alertes pour recevoir des notifications en cas de coupure.

### Récupération de mot de passe

1. Depuis la page de connexion, cliquez sur **Mot de passe oublié ?**
2. Renseignez votre nom d'utilisateur et votre email de récupération
3. **Si SMTP configuré** : un code à 6 chiffres est envoyé par email (valable 15 minutes)
4. **Si SMTP non configuré** : si l'email est reconnu, accès direct au changement de mot de passe

L'email de récupération se configure dans **Paramètres** → onglet **Compte**.

### Mise à jour depuis GitHub

Depuis **Paramètres** → onglet **Mise à jour**, le dépôt `YakuMawi/freebox-monitor` est pré-configuré. Cliquez sur **Vérifier les mises à jour** pour détecter et appliquer les nouvelles versions.

## Structure

```
freebox-monitor/
├── monitor.py          # Serveur Flask + collecte de données
├── db.py               # Couche SQLite
├── alerts.py           # Alertes SMTP + envoi codes OTP
├── updater.py          # Mise à jour GitHub
├── auth.py             # Autorisation Freebox
├── config.json         # Configuration par défaut
├── requirements.txt    # Dépendances Python
├── VERSION             # Version courante
├── install.sh          # Script d'installation
├── templates/
│   ├── index.html          # Dashboard principal
│   ├── settings.html       # Paramètres (SMTP, Mise à jour, Compte)
│   ├── login.html          # Page de connexion
│   ├── setup.html          # Création du compte initial
│   ├── forgot_password.html # Récupération de compte
│   └── reset_password.html  # Réinitialisation du mot de passe
├── data/               # Base SQLite (gitignored)
├── certs/              # Certificats SSL (gitignored)
└── credentials.json    # Token Freebox (gitignored)
```

## Désinstallation

```bash
# Arrêter et désactiver le service
systemctl stop freebox-monitor
systemctl disable freebox-monitor

# Supprimer le fichier de service
rm /etc/systemd/system/freebox-monitor.service
systemctl daemon-reload

# Supprimer le répertoire du projet
rm -rf /root/freebox-monitor
```

> Les données (base SQLite, certificats SSL, credentials Freebox) sont dans `/root/freebox-monitor/` et seront supprimées avec le répertoire.

## Sécurité

- Mots de passe hashés avec Werkzeug (pbkdf2-sha256)
- Cookies de session : `Secure`, `HttpOnly`, `SameSite=Lax`
- En-têtes HTTP : `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`
- Rate limiting : 5 tentatives / 5 min sur `/login`, 3 tentatives / 10 min sur `/forgot-password`
- Codes OTP générés avec `secrets` (cryptographiquement sûrs), expiration 15 min, usage unique
- Flux de récupération de compte géré côté serveur (session Flask signée), pas de paramètre forgeable côté client
- Permissions fichiers : `credentials.json` et base SQLite en `600`, répertoires sensibles en `700`
- Secrets SMTP et GitHub token masqués dans l'interface

## Licence

MIT
