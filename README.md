# Freebox Monitor

Dashboard de monitoring temps réel pour routeurs Freebox (Delta, Pop, Ultra, Révolution...).
Déployé en tant que service systemd sur un serveur Linux, accessible depuis n'importe quel navigateur via HTTPS.

> 🇬🇧 [English version below](#freebox-monitor-english)

---

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Prérequis](#prérequis)
- [Installation en une commande](#installation-en-une-commande)
- [Installation classique](#installation-classique-git-clone)
- [Installation manuelle](#installation-manuelle)
- [Premier lancement](#premier-lancement)
- [Configuration](#configuration)
  - [Compte administrateur](#compte-administrateur)
  - [Alertes SMTP](#alertes-smtp)
  - [Webhooks](#webhooks)
  - [Récupération de mot de passe](#récupération-de-mot-de-passe)
  - [Mises à jour et rétrogradation](#mises-à-jour-et-rétrogradation)
  - [Port d'écoute](#port-découte)
  - [Thème clair / sombre](#thème-clair--sombre)
- [Commandes utiles](#commandes-utiles)
- [Structure du projet](#structure-du-projet)
- [Architecture technique](#architecture-technique)
- [Sécurité](#sécurité)
- [Désinstallation](#désinstallation)
- [Historique des versions](#historique-des-versions)
- [Licence](#licence)

---

## Fonctionnalités

### Dashboard temps réel
- **Connexion** — état (up/down), débit descendant/montant en Mbit/s, bande passante max, données transférées
- **Système** — modèle Freebox, version firmware, temps de fonctionnement
- **Températures** — CPU (master, AP, slave), sondes T1/T2/T3, disque HDD, avec code couleur (vert → orange → rouge)
- **Ventilateurs** — vitesse en RPM avec indicateur visuel
- **Réseau LAN** — nombre d'hôtes actifs
- **Switch intégré** — état des ports, vitesse, duplex, statistiques RX/TX
- **Stockage** — disques et partitions avec capacité, espace libre, température, barre de progression
- **FTTH/Fibre** — état du SFP, puissance TX/RX, modèle et fabricant

### Graphiques et historique
- Graphique du débit en temps réel (descendant + montant) sur fenêtre glissante
- Graphique des températures CPU et disque
- Historique conservé jusqu'à **1 an** en base SQLite
- Statistiques par période : 24h, 7j, 30j, 60j, 90j, 1 an

### Disponibilité et rapports
- **Calendrier mensuel** avec taux de disponibilité par jour (code couleur)
- **Rapport mensuel** imprimable / exportable en PDF avec calendrier, statistiques et journal des coupures
- **Journal des coupures** avec pagination, date de début, fin, durée et cause

### Alertes email
- Notification de **perte de connexion** (délai configurable, défaut 30 secondes)
- Notification de **rétablissement** avec durée de la coupure
- Email de test depuis l'interface
- Support SMTP avec STARTTLS (port 587) ou SSL direct (port 465)

### Authentification et sécurité
- Compte administrateur unique protégé par mot de passe
- Récupération de compte par email (code OTP ou accès direct selon SMTP)
- SSL/HTTPS avec certificat auto-signé généré à l'installation
- **Chiffrement Fernet** des secrets sensibles au repos (mot de passe SMTP, token GitHub, token Freebox)
- **Rate limiting persistant** en base SQLite (résiste aux redémarrages du service)
- Thème clair et sombre avec persistance de la préférence

### Mises à jour
- **Pop-up automatique** après connexion si une nouvelle version est disponible sur GitHub
- Bouton **"Mettre à jour maintenant"** directement dans le pop-up
- Vérification et application de la mise à jour depuis les Paramètres
- **Rétrogradation** vers n'importe quelle version antérieure depuis l'interface
- Rechargement automatique de la page après redémarrage du service

---

## Prérequis

| Composant | Version minimale | Notes |
|---|---|---|
| Linux | — | Debian / Ubuntu recommandé |
| systemd | — | Requis pour le service |
| Python | 3.10+ | Installé automatiquement si absent |
| python3-venv | — | Installé automatiquement si absent |
| OpenSSL | — | Installé automatiquement si absent |
| curl | — | Pour la méthode d'installation one-liner |
| git | — | Installé automatiquement par bootstrap.sh |

La Freebox doit être accessible depuis le serveur sur `http://mafreebox.freebox.fr`.

---

## Installation en une commande

La méthode la plus simple. Une seule commande depuis n'importe quelle machine Debian/Ubuntu avec accès root :

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YakuMawi/freebox-monitor/main/bootstrap.sh)
```

Ce que fait le script `bootstrap.sh` :
1. Installe `git` si absent (via `apt-get`)
2. Clone le dépôt dans `/root/freebox-monitor`
3. Lance `install.sh` automatiquement

---

## Installation classique (git clone)

```bash
git clone https://github.com/YakuMawi/freebox-monitor.git
cd freebox-monitor
bash install.sh
```

Ce que fait `install.sh` :
1. Installe les dépendances système manquantes (`python3`, `python3-venv`, `openssl`) via `apt-get`
2. Demande le **port d'écoute** (défaut : `8000`)
3. Crée un environnement virtuel Python (`venv/`) et installe les dépendances Python
4. Génère un **certificat SSL auto-signé** valable 10 ans dans `certs/`
5. Lance l'**autorisation Freebox** — vous devrez valider sur l'écran LCD de votre Freebox
6. Crée et démarre le **service systemd** `freebox-monitor`
7. Affiche l'URL d'accès

> Le script doit être lancé en tant que **root** (pas besoin de `sudo`).

---

## Installation manuelle

Pour les environnements sans `apt-get` ou pour une configuration personnalisée :

```bash
git clone https://github.com/YakuMawi/freebox-monitor.git
cd freebox-monitor

# Environnement virtuel Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Certificat SSL auto-signé (optionnel mais recommandé)
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout certs/key.pem -out certs/cert.pem \
    -days 3650 -subj "/CN=freebox-monitor/O=Freebox Monitor/C=FR"

# Autorisation Freebox (à réaliser une seule fois)
python3 auth.py

# Lancement direct
python3 monitor.py
```

> Sans certificat SSL, l'application démarre en HTTP sur le port configuré.

---

## Premier lancement

1. Ouvrez votre navigateur et accédez à `https://<ip-du-serveur>:8000`
   *(remplacez `8000` par le port choisi à l'installation)*
2. Votre navigateur affichera un avertissement de sécurité dû au certificat auto-signé — cliquez sur **Avancé** puis **Continuer**
3. La page de **création du compte administrateur** s'affiche automatiquement au premier accès
4. Renseignez un nom d'utilisateur, un mot de passe et, de préférence, un **email de récupération**
5. Vous êtes connecté et le dashboard s'affiche avec les données de votre Freebox

---

## Configuration

### Compte administrateur

Accédez à **Paramètres** → onglet **Compte** pour :
- Modifier votre mot de passe (le mot de passe actuel est requis)
- Enregistrer ou modifier votre **email de récupération**

L'email de récupération est utilisé pour réinitialiser le mot de passe en cas d'oubli.

---

### Alertes SMTP

Accédez à **Paramètres** → onglet **Alertes SMTP** :

| Champ | Description | Exemple |
|---|---|---|
| Serveur SMTP | Adresse du serveur mail | `smtp.gmail.com` |
| Port | Port SMTP | `587` (STARTTLS) ou `465` (SSL) |
| Utilisateur | Identifiant de connexion | `vous@gmail.com` |
| Mot de passe | Mot de passe SMTP | Mot de passe d'application Gmail |
| Expéditeur | Adresse affichée dans l'email | `freebox@moi.fr` |
| Destinataire | Email qui reçoit les alertes | `moi@gmail.com` |
| STARTTLS | Chiffrement sur port 587 | Activé par défaut |
| SSL | Chiffrement direct sur port 465 | Désactivé par défaut |
| Délai avant alerte | Secondes avant l'envoi (évite les fausses alertes) | `30` |

> Utilisez le bouton **Tester l'envoi** pour vérifier la configuration avant d'activer les alertes.

**Configuration Gmail** : créez un [mot de passe d'application](https://myaccount.google.com/apppasswords) (nécessite la validation en deux étapes activée).

> Le mot de passe SMTP et le token GitHub sont **chiffrés automatiquement** (Fernet AES-128-CBC) avant stockage en base de données.

---

### Webhooks

Accédez à **Paramètres** → onglet **Webhooks** pour envoyer les alertes vers des services tiers :

| Service | Format |
|---|---|
| Discord | POST JSON `{"content": "..."}` vers l'URL webhook Discord |
| Google Chat | POST JSON `{"text": "..."}` vers l'URL webhook Google Chat |
| Microsoft Teams | POST JSON `{"text": "..."}` via Incoming Webhook Teams |
| Synology Chat | POST JSON vers l'API externe Synology Chat |
| Générique (JSON) | POST JSON `{"event", "title", "message", "timestamp"}` vers n'importe quelle URL |

> Utilisez le bouton **Tester** à côté de chaque URL pour vérifier la configuration. Le badge **OK** ou **Erreur** s'affiche après le test.

---

### Récupération de mot de passe

Depuis la page de connexion, cliquez sur **Mot de passe oublié ?**

**Avec SMTP configuré :**
1. Saisissez votre nom d'utilisateur et votre email de récupération
2. Un **code à 6 chiffres** est envoyé à votre email (valable **15 minutes**, usage unique)
3. Saisissez le code et votre nouveau mot de passe

**Sans SMTP configuré :**
1. Saisissez votre nom d'utilisateur et votre email de récupération
2. Si l'email est reconnu, accès direct au formulaire de changement de mot de passe
3. Saisissez et confirmez votre nouveau mot de passe

> L'email de récupération doit avoir été enregistré au préalable dans **Paramètres → Compte** ou lors de la création du compte.

---

### Mises à jour et rétrogradation

**Pop-up automatique après connexion :**
Dès que vous vous connectez, le dashboard vérifie silencieusement si une nouvelle version est disponible sur GitHub. Si c'est le cas, un pop-up s'affiche avec la version actuelle, la version disponible, les notes de version et un bouton **"Mettre à jour maintenant"**. Cliquez sur **"Plus tard"** pour fermer sans mettre à jour (le pop-up ne réapparaît pas pendant la session courante).

**Mise à jour manuelle depuis les Paramètres :**

Accédez à **Paramètres** → onglet **Mise à jour** :
1. Cliquez sur **Vérifier les mises à jour** — le dépôt `YakuMawi/freebox-monitor` est pré-configuré
2. Si une version plus récente est disponible, le bouton **Mettre à jour** apparaît
3. Cliquez sur **Mettre à jour** — la mise à jour s'applique et le service redémarre
4. La page se recharge automatiquement dès que le service est de nouveau disponible

**Rétrogradation vers une version antérieure :**
1. Cliquez sur **Charger les versions** pour afficher toutes les releases disponibles
2. Sélectionnez la version souhaitée dans la liste déroulante
3. Cliquez sur **Rétrograder** — le service redémarre sur la version choisie

> Un backup des fichiers courants est créé dans `data/backup_before_update/` avant chaque opération.
> Les fichiers protégés ne sont jamais écrasés : `data/`, `certs/`, `credentials.json`, `venv/`.

---

### Clé de chiffrement des secrets (avancé)

Par défaut, une clé Fernet est générée automatiquement au premier démarrage et stockée dans `data/.secret_key` (chmod 600). Pour une sécurité maximale, vous pouvez fournir votre propre clé via une variable d'environnement :

```bash
# Générer une clé Fernet
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# L'injecter dans le service systemd
# Ajouter dans /etc/systemd/system/freebox-monitor.service :
# [Service]
# EnvironmentFile=-/etc/freebox-monitor/secrets.env
#
# Créer /etc/freebox-monitor/secrets.env (chmod 600) :
# FBX_MASTER_KEY=<votre_clé_base64>
```

> Si `FBX_MASTER_KEY` est définie, elle a priorité sur `data/.secret_key`.
> Ne perdez pas cette clé — les secrets en base seraient illisibles sans elle.

---

### Port d'écoute

Le port est choisi lors de l'installation (défaut : `8000`). Pour le modifier après installation :

```bash
cd /root/freebox-monitor
venv/bin/python3 -c "import db; db.init_db(); db.set_config('port', '9000')"
systemctl restart freebox-monitor
```

> Pensez à ouvrir le port dans votre pare-feu si nécessaire.

---

### Thème clair / sombre

Un bouton **☀️ / 🌙** est disponible sur toutes les pages (dashboard, paramètres, connexion).
La préférence est sauvegardée dans le `localStorage` du navigateur et s'applique immédiatement sans rechargement.

---

## Commandes utiles

```bash
# État du service
systemctl status freebox-monitor

# Redémarrer le service
systemctl restart freebox-monitor

# Arrêter le service
systemctl stop freebox-monitor

# Suivre les logs en temps réel
journalctl -u freebox-monitor -f

# Voir les 50 dernières lignes de logs
journalctl -u freebox-monitor -n 50

# Vérifier la version installée
cat /root/freebox-monitor/VERSION
```

---

## Structure du projet

```
freebox-monitor/
├── monitor.py               # Serveur Flask, collecte des données, routes API
├── db.py                    # Couche d'accès SQLite (métriques, config, users, OTP, rate limits)
├── crypto.py                # Chiffrement/déchiffrement Fernet des secrets sensibles
├── alerts.py                # Envoi d'alertes SMTP (coupure, rétablissement, OTP, test)
├── updater.py               # Vérification et application des mises à jour GitHub
├── auth.py                  # Autorisation Freebox (one-shot, génère credentials.json)
├── bootstrap.sh             # Script d'installation one-liner (curl)
├── install.sh               # Script d'installation complet
├── uninstall.sh             # Script de désinstallation guidé
├── config.json              # Valeurs par défaut de la configuration
├── requirements.txt         # Dépendances Python (flask, requests, cryptography)
├── VERSION                  # Version courante du logiciel
├── templates/
│   ├── index.html           # Dashboard principal (pop-up mise à jour inclus)
│   ├── settings.html        # Page paramètres (SMTP, Webhooks, Mise à jour, Compte)
│   ├── login.html           # Page de connexion
│   ├── setup.html           # Création du compte initial
│   ├── forgot_password.html # Demande de réinitialisation de mot de passe
│   └── reset_password.html  # Formulaire de réinitialisation
├── data/                    # Base SQLite freebox.db + clé chiffrement — gitignored
├── certs/                   # Certificats SSL (cert.pem, key.pem) — gitignored
└── credentials.json         # Token d'authentification Freebox (chiffré) — gitignored
```

---

## Architecture technique

| Composant | Technologie |
|---|---|
| Backend | Python 3.10+ / Flask 3.0 |
| Base de données | SQLite 3 (via module `sqlite3`) |
| Chiffrement secrets | `cryptography` — Fernet (AES-128-CBC + HMAC-SHA256) |
| Collecte des données | API Freebox v8 (HTTP, toutes les 10 secondes) |
| Authentification Freebox | HMAC-SHA1 sur challenge (session renouvelée toutes les 25 min) |
| Frontend | HTML5 / CSS3 / JavaScript vanilla |
| Graphiques | Chart.js 4.4.0 |
| Service système | systemd |
| SSL | OpenSSL, certificat auto-signé RSA 2048 bits |

**Base de données — tables principales :**

| Table | Contenu |
|---|---|
| `metrics` | Métriques toutes les 10 secondes (débit, températures, fans, LAN, uptime) |
| `outages` | Journal des coupures (début, fin, durée, cause, flag test) |
| `config` | Configuration clé/valeur (secrets chiffrés, SMTP, GitHub, port…) |
| `users` | Compte administrateur (username, hash pbkdf2, email de récupération) |
| `reset_codes` | Codes OTP de réinitialisation (username, code, expiration, utilisé) |
| `rate_limits` | Tentatives de connexion par IP/action — persistant entre redémarrages |

**Rétention des données :** nettoyage automatique hebdomadaire, données conservées 1 an.

---

## Sécurité

| Mesure | Détail |
|---|---|
| Hachage des mots de passe | Werkzeug pbkdf2-sha256 avec sel aléatoire |
| Chiffrement des secrets | Fernet AES-128-CBC + HMAC-SHA256 — smtp_password, github_token, app_token Freebox |
| Clé maître | Fichier `data/.secret_key` (chmod 600) ou variable `FBX_MASTER_KEY` |
| Cookies de session | `Secure`, `HttpOnly`, `SameSite=Lax` |
| En-têtes HTTP | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` |
| CSP | `Content-Security-Policy` avec nonce par requête |
| Protection CSRF | Token aléatoire par session sur toutes les routes POST sensibles |
| Rate limiting | Persistant en SQLite — 5 tentatives/5 min (login), 3/10 min (forgot), 5/15 min (OTP) |
| Codes OTP | Générés avec `secrets.randbelow()` (CSPRNG), expiration 15 min, usage unique |
| Flux de récupération | Vérification d'identité en session Flask signée côté serveur, aucun paramètre forgeable |
| Permissions fichiers | `credentials.json`, `freebox.db` et `.secret_key` en `600` |
| Secrets dans l'interface | SMTP password et GitHub token masqués (`••••••••`) dans les réponses API |
| Injection SQL | Requêtes paramétrées sur toutes les requêtes SQLite |
| XSS | Jinja2 avec auto-escape activé sur tous les templates |
| Transport | HTTPS avec certificat SSL, HTTP désactivé si certificat présent |

---

## Désinstallation

Un script de désinstallation guidé est inclus. Depuis le répertoire d'installation :

```bash
bash /root/freebox-monitor/uninstall.sh
```

Ou directement depuis GitHub (même si le dossier a été partiellement supprimé) :

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YakuMawi/freebox-monitor/main/uninstall.sh)
```

Le script effectue dans l'ordre :
1. **Arrêt** du service s'il est actif
2. **Désactivation** du démarrage automatique
3. **Suppression** du fichier `/etc/systemd/system/freebox-monitor.service` et rechargement du daemon
4. **Proposition de révoquer** le token Freebox (avec instructions pour le faire manuellement depuis l'interface Freebox)
5. **Confirmation avant suppression** du répertoire `/root/freebox-monitor` (base de données, config, certificats)

> Cette opération est irréversible : toutes les données (historique des métriques, journal des coupures, configuration, credentials Freebox et certificats SSL) sont définitivement supprimées.

---

## Historique des versions

| Version | Points clés |
|---|---|
| **1.1.4** | Correction rapport mensuel — CSS bloqué par CSP (ajout nonce sur `<style>` et `<script>`), mauvais logo remplacé par le SVG Freebox, `print-color-adjust:exact` pour les couleurs du calendrier à l'impression, bouton Imprimer migré vers `addEventListener` |
| **1.1.3** | Correction onglets Paramètres (Webhooks, Mise à jour, Compte) inaccessibles — conformité CSP étendue à `settings.html` (migration `onclick` inline → `addEventListener`) ; ajout du script de désinstallation guidé `uninstall.sh` |
| **1.1.2** | Audit sécurité complet : rate limiting OTP renforcé, conformité CSP (nonce par requête, suppression des `onclick` inline), cookies `SameSite=Lax`/`Secure`/`HttpOnly`, en-têtes HTTP additionnels, correction installation venv Python 3.12+ (`python3.x-venv` version-spécifique) |
| **1.1.0** | Chiffrement Fernet des secrets au repos (smtp_password, github_token, app_token Freebox) ; rate limiting persistant en SQLite (résiste aux redémarrages) ; pop-up automatique de mise à jour après connexion avec bouton de mise à jour directe |
| **1.0.8** | Mode test : différenciation coupures volontaires / pannes réelles, bouton mode test avec bannière, reset compteur par journée, badge et note par coupure dans le journal, statistiques séparées test/réel, calendrier corrigé en thème clair |
| **1.0.7** | Correction fiabilité des statistiques de coupure : la ré-authentification Freebox est invalidée dès qu'une requête échoue, ce qui permet au monitor de détecter la remontée de la box sans rester bloqué sur un token de session périmé |
| **1.0.6** | Thème clair/sombre sur toutes les pages, auto-refresh après MAJ, downgrade depuis l'interface, repo GitHub pré-configuré |
| **1.0.5** | Installation one-liner via curl (`bootstrap.sh`), installation automatique des dépendances système, section désinstallation |
| **1.0.4** | Correction compatibilité Python < 3.12 (SyntaxError f-string imbriqué) |
| **1.0.3** | Port configurable à l'installation, suppression des `sudo` dans `install.sh` |
| **1.0.1** | Récupération de compte (OTP email + mode sans SMTP), rate limiting, flags cookies, en-têtes HTTP sécurité, bouton déconnexion dashboard |
| **1.0.0** | Version initiale — dashboard, alertes SMTP, historique, rapports, SSL, authentification, mise à jour GitHub |

---

## Licence

MIT — libre d'utilisation, de modification et de distribution.

---
---

# Freebox Monitor (English)

Real-time monitoring dashboard for Freebox routers (Delta, Pop, Ultra, Révolution...).
Deployed as a systemd service on a Linux server, accessible from any browser via HTTPS.

> 🇫🇷 [Version française ci-dessus](#freebox-monitor)

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [One-command Installation](#one-command-installation)
- [Standard Installation](#standard-installation-git-clone)
- [Manual Installation](#manual-installation)
- [First Launch](#first-launch)
- [Configuration](#configuration-1)
  - [Administrator Account](#administrator-account)
  - [SMTP Alerts](#smtp-alerts)
  - [Webhooks](#webhooks-1)
  - [Password Recovery](#password-recovery)
  - [Updates and Downgrade](#updates-and-downgrade)
  - [Listening Port](#listening-port)
  - [Light / Dark Theme](#light--dark-theme)
- [Useful Commands](#useful-commands)
- [Project Structure](#project-structure)
- [Technical Architecture](#technical-architecture)
- [Security](#security)
- [Uninstallation](#uninstallation)
- [Changelog](#changelog)
- [License](#license)

---

## Features

### Real-time Dashboard
- **Connection** — state (up/down), download/upload speed in Mbit/s, max bandwidth, transferred data
- **System** — Freebox model, firmware version, uptime
- **Temperatures** — CPU (master, AP, slave), probes T1/T2/T3, HDD disk, with color coding (green → orange → red)
- **Fans** — speed in RPM with visual indicator
- **LAN Network** — number of active hosts
- **Built-in Switch** — port status, speed, duplex, RX/TX statistics
- **Storage** — drives and partitions with capacity, free space, temperature, progress bar
- **FTTH/Fiber** — SFP status, TX/RX power, model and manufacturer

### Charts and History
- Real-time bandwidth chart (download + upload) on sliding window
- CPU and disk temperature chart
- History stored up to **1 year** in SQLite database
- Statistics by period: 24h, 7d, 30d, 60d, 90d, 1 year

### Availability and Reports
- **Monthly calendar** with daily availability rate (color coded)
- **Monthly report** printable / exportable as PDF with calendar, statistics and outage log
- **Outage log** with pagination, start date, end date, duration and cause

### Email Alerts
- **Connection loss** notification (configurable delay, default 30 seconds)
- **Reconnection** notification with outage duration
- Test email from the interface
- SMTP support with STARTTLS (port 587) or direct SSL (port 465)

### Authentication and Security
- Single administrator account protected by password
- Account recovery by email (OTP code or direct access depending on SMTP)
- SSL/HTTPS with self-signed certificate generated at installation
- **Fernet encryption** of sensitive secrets at rest (SMTP password, GitHub token, Freebox token)
- **Persistent rate limiting** in SQLite database (survives service restarts)
- Light and dark theme with preference persistence

### Updates
- **Automatic pop-up** after login if a new version is available on GitHub
- **"Update now"** button directly in the pop-up
- Update check and application from the Settings page
- **Downgrade** to any previous version from the interface
- Automatic page reload after service restart

---

## Requirements

| Component | Minimum version | Notes |
|---|---|---|
| Linux | — | Debian / Ubuntu recommended |
| systemd | — | Required for the service |
| Python | 3.10+ | Automatically installed if absent |
| python3-venv | — | Automatically installed if absent |
| OpenSSL | — | Automatically installed if absent |
| curl | — | For one-liner installation method |
| git | — | Automatically installed by bootstrap.sh |

The Freebox must be accessible from the server at `http://mafreebox.freebox.fr`.

---

## One-command Installation

The simplest method. A single command from any Debian/Ubuntu machine with root access:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YakuMawi/freebox-monitor/main/bootstrap.sh)
```

What `bootstrap.sh` does:
1. Installs `git` if absent (via `apt-get`)
2. Clones the repository into `/root/freebox-monitor`
3. Runs `install.sh` automatically

---

## Standard Installation (git clone)

```bash
git clone https://github.com/YakuMawi/freebox-monitor.git
cd freebox-monitor
bash install.sh
```

What `install.sh` does:
1. Installs missing system dependencies (`python3`, `python3-venv`, `openssl`) via `apt-get`
2. Asks for the **listening port** (default: `8000`)
3. Creates a Python virtual environment (`venv/`) and installs Python dependencies
4. Generates a **self-signed SSL certificate** valid for 10 years in `certs/`
5. Runs **Freebox authorization** — you will need to validate on your Freebox LCD screen
6. Creates and starts the `freebox-monitor` **systemd service**
7. Displays the access URL

> The script must be run as **root** (no `sudo` needed).

---

## Manual Installation

For environments without `apt-get` or for custom configuration:

```bash
git clone https://github.com/YakuMawi/freebox-monitor.git
cd freebox-monitor

# Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Self-signed SSL certificate (optional but recommended)
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout certs/key.pem -out certs/cert.pem \
    -days 3650 -subj "/CN=freebox-monitor/O=Freebox Monitor/C=FR"

# Freebox authorization (run once)
python3 auth.py

# Direct launch
python3 monitor.py
```

> Without an SSL certificate, the application starts in HTTP on the configured port.

---

## First Launch

1. Open your browser and go to `https://<server-ip>:8000`
   *(replace `8000` with the port chosen at installation)*
2. Your browser will show a security warning due to the self-signed certificate — click **Advanced** then **Continue**
3. The **administrator account creation** page appears automatically on first access
4. Enter a username, a password and preferably a **recovery email**
5. You are logged in and the dashboard shows your Freebox data

---

## Configuration

### Administrator Account

Go to **Settings** → **Account** tab to:
- Change your password (current password required)
- Register or update your **recovery email**

The recovery email is used to reset your password if forgotten.

---

### SMTP Alerts

Go to **Settings** → **SMTP Alerts** tab:

| Field | Description | Example |
|---|---|---|
| SMTP Server | Mail server address | `smtp.gmail.com` |
| Port | SMTP port | `587` (STARTTLS) or `465` (SSL) |
| Username | Login credentials | `you@gmail.com` |
| Password | SMTP password | Gmail app password |
| Sender | Address shown in the email | `freebox@me.com` |
| Recipient | Email that receives alerts | `me@gmail.com` |
| STARTTLS | Encryption on port 587 | Enabled by default |
| SSL | Direct encryption on port 465 | Disabled by default |
| Alert delay | Seconds before sending (avoids false alerts) | `30` |

> Use the **Test email** button to verify the configuration before enabling alerts.

**Gmail setup**: create an [app password](https://myaccount.google.com/apppasswords) (requires two-step verification enabled).

> The SMTP password and GitHub token are **automatically encrypted** (Fernet AES-128-CBC) before being stored in the database.

---

### Webhooks

Go to **Settings** → **Webhooks** tab to send alerts to third-party services:

| Service | Format |
|---|---|
| Discord | POST JSON `{"content": "..."}` to the Discord webhook URL |
| Google Chat | POST JSON `{"text": "..."}` to the Google Chat webhook URL |
| Microsoft Teams | POST JSON `{"text": "..."}` via Teams Incoming Webhook |
| Synology Chat | POST JSON to the Synology Chat external API |
| Generic (JSON) | POST JSON `{"event", "title", "message", "timestamp"}` to any URL |

> Use the **Test** button next to each URL to verify the configuration. An **OK** or **Error** badge appears after the test.

---

### Password Recovery

From the login page, click **Forgot password?**

**With SMTP configured:**
1. Enter your username and recovery email
2. A **6-digit code** is sent to your email (valid for **15 minutes**, single use)
3. Enter the code and your new password

**Without SMTP configured:**
1. Enter your username and recovery email
2. If the email is recognized, direct access to the password change form
3. Enter and confirm your new password

> The recovery email must have been previously registered in **Settings → Account** or during account creation.

---

### Updates and Downgrade

**Automatic pop-up after login:**
As soon as you log in, the dashboard silently checks if a new version is available on GitHub. If so, a pop-up appears with the current version, the available version, the release notes and an **"Update now"** button. Click **"Later"** to dismiss without updating (the pop-up does not reappear during the current session).

**Manual update from Settings:**

Go to **Settings** → **Update** tab:
1. Click **Check for updates** — the `YakuMawi/freebox-monitor` repository is pre-configured
2. If a newer version is available, the **Update** button appears
3. Click **Update** — the update is applied and the service restarts
4. The page reloads automatically once the service is back online

**Downgrade to a previous version:**
1. Click **Load versions** to display all available releases
2. Select the desired version from the dropdown list
3. Click **Downgrade** — the service restarts on the chosen version

> A backup of current files is created in `data/backup_before_update/` before each operation.
> Protected files are never overwritten: `data/`, `certs/`, `credentials.json`, `venv/`.

---

### Encryption Key (advanced)

By default, a Fernet key is automatically generated on first startup and stored in `data/.secret_key` (chmod 600). For maximum security, you can provide your own key via an environment variable:

```bash
# Generate a Fernet key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Inject it into the systemd service
# Add to /etc/systemd/system/freebox-monitor.service:
# [Service]
# EnvironmentFile=-/etc/freebox-monitor/secrets.env
#
# Create /etc/freebox-monitor/secrets.env (chmod 600):
# FBX_MASTER_KEY=<your_base64_key>
```

> If `FBX_MASTER_KEY` is set, it takes priority over `data/.secret_key`.
> Do not lose this key — secrets in the database would be unreadable without it.

---

### Listening Port

The port is chosen at installation (default: `8000`). To change it after installation:

```bash
cd /root/freebox-monitor
venv/bin/python3 -c "import db; db.init_db(); db.set_config('port', '9000')"
systemctl restart freebox-monitor
```

> Remember to open the port in your firewall if necessary.

---

### Light / Dark Theme

A **☀️ / 🌙** button is available on all pages (dashboard, settings, login).
The preference is saved in the browser's `localStorage` and applies immediately without reloading.

---

## Useful Commands

```bash
# Service status
systemctl status freebox-monitor

# Restart service
systemctl restart freebox-monitor

# Stop service
systemctl stop freebox-monitor

# Follow logs in real time
journalctl -u freebox-monitor -f

# View last 50 lines of logs
journalctl -u freebox-monitor -n 50

# Check installed version
cat /root/freebox-monitor/VERSION
```

---

## Project Structure

```
freebox-monitor/
├── monitor.py               # Flask server, data collection, API routes
├── db.py                    # SQLite access layer (metrics, config, users, OTP, rate limits)
├── crypto.py                # Fernet encryption/decryption for sensitive secrets
├── alerts.py                # SMTP alert sending (outage, reconnection, OTP, test)
├── updater.py               # GitHub update check and application
├── auth.py                  # Freebox authorization (one-shot, generates credentials.json)
├── bootstrap.sh             # One-liner installation script (curl)
├── install.sh               # Full installation script
├── uninstall.sh             # Guided uninstall script
├── config.json              # Default configuration values
├── requirements.txt         # Python dependencies (flask, requests, cryptography)
├── VERSION                  # Current software version
├── templates/
│   ├── index.html           # Main dashboard (update pop-up included)
│   ├── settings.html        # Settings page (SMTP, Webhooks, Update, Account)
│   ├── login.html           # Login page
│   ├── setup.html           # Initial account creation
│   ├── forgot_password.html # Password reset request
│   └── reset_password.html  # Password reset form
├── data/                    # SQLite freebox.db + encryption key — gitignored
├── certs/                   # SSL certificates (cert.pem, key.pem) — gitignored
└── credentials.json         # Freebox authentication token (encrypted) — gitignored
```

---

## Technical Architecture

| Component | Technology |
|---|---|
| Backend | Python 3.10+ / Flask 3.0 |
| Database | SQLite 3 (via `sqlite3` module) |
| Secret encryption | `cryptography` — Fernet (AES-128-CBC + HMAC-SHA256) |
| Data collection | Freebox API v8 (HTTP, every 10 seconds) |
| Freebox authentication | HMAC-SHA1 on challenge (session renewed every 25 min) |
| Frontend | HTML5 / CSS3 / vanilla JavaScript |
| Charts | Chart.js 4.4.0 |
| System service | systemd |
| SSL | OpenSSL, self-signed RSA 2048-bit certificate |

**Database — main tables:**

| Table | Content |
|---|---|
| `metrics` | Metrics every 10 seconds (bandwidth, temperatures, fans, LAN, uptime) |
| `outages` | Outage log (start, end, duration, cause, test flag) |
| `config` | Key/value configuration (encrypted secrets, SMTP, GitHub, port…) |
| `users` | Administrator account (username, pbkdf2 hash, recovery email) |
| `reset_codes` | OTP reset codes (username, code, expiration, used) |
| `rate_limits` | Connection attempts by IP/action — persistent across restarts |

**Data retention:** automatic weekly cleanup, data retained for 1 year.

---

## Security

| Measure | Detail |
|---|---|
| Password hashing | Werkzeug pbkdf2-sha256 with random salt |
| Secret encryption | Fernet AES-128-CBC + HMAC-SHA256 — smtp_password, github_token, Freebox app_token |
| Master key | `data/.secret_key` file (chmod 600) or `FBX_MASTER_KEY` environment variable |
| Session cookies | `Secure`, `HttpOnly`, `SameSite=Lax` |
| HTTP headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` |
| CSP | `Content-Security-Policy` with per-request nonce |
| CSRF protection | Random per-session token on all sensitive POST routes |
| Rate limiting | Persistent in SQLite — 5 attempts/5 min (login), 3/10 min (forgot), 5/15 min (OTP) |
| OTP codes | Generated with `secrets.randbelow()` (CSPRNG), 15 min expiry, single use |
| Recovery flow | Identity check stored in server-side signed Flask session, no forgeable parameter |
| File permissions | `credentials.json`, `freebox.db` and `.secret_key` set to `600` |
| Secrets in UI | SMTP password and GitHub token masked (`••••••••`) in API responses |
| SQL injection | Parameterized queries on all SQLite queries |
| XSS | Jinja2 with auto-escape enabled on all templates |
| Transport | HTTPS with SSL certificate, HTTP disabled if certificate present |

---

## Uninstallation

A guided uninstall script is included. From the installation directory:

```bash
bash /root/freebox-monitor/uninstall.sh
```

Or directly from GitHub (even if the folder has been partially deleted):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YakuMawi/freebox-monitor/main/uninstall.sh)
```

The script performs in order:
1. **Stops** the service if active
2. **Disables** automatic startup
3. **Removes** `/etc/systemd/system/freebox-monitor.service` and reloads the daemon
4. **Offers to revoke** the Freebox token (with instructions to do it manually from the Freebox interface)
5. **Asks for confirmation** before deleting `/root/freebox-monitor` (database, config, certificates)

> This operation is irreversible: all data (metrics history, outage log, configuration, Freebox credentials and SSL certificates) is permanently deleted.

---

## Changelog

| Version | Highlights |
|---|---|
| **1.1.4** | Fix monthly report — CSS blocked by CSP (added nonce on `<style>` and `<script>`), wrong logo replaced with Freebox SVG, `print-color-adjust:exact` for calendar colors when printing, print button migrated to `addEventListener` |
| **1.1.3** | Fix Settings tabs (Webhooks, Update, Account) being inaccessible — CSP compliance extended to `settings.html` (migrated inline `onclick` → `addEventListener`); added guided uninstall script `uninstall.sh` |
| **1.1.2** | Full security audit: hardened OTP rate limiting, CSP compliance (per-request nonce, removed inline `onclick`), `SameSite=Lax`/`Secure`/`HttpOnly` cookies, additional HTTP headers, fix venv installation on Python 3.12+ (version-specific `python3.x-venv` package) |
| **1.1.0** | Fernet encryption of secrets at rest (smtp_password, github_token, Freebox app_token); persistent SQLite rate limiting (survives restarts); automatic update pop-up after login with direct update button |
| **1.0.8** | Test mode: differentiating planned outages from real failures, test mode button with banner, counter reset by day, badge and note per outage in the log, separate test/real statistics, calendar fix in light theme |
| **1.0.7** | Fix outage detection reliability: Freebox re-authentication is invalidated as soon as a request fails, allowing the monitor to detect reconnection without getting stuck on a stale session token |
| **1.0.6** | Light/dark theme on all pages, auto-refresh after update, downgrade from UI, GitHub repo pre-configured |
| **1.0.5** | One-liner installation via curl (`bootstrap.sh`), automatic system dependency installation |
| **1.0.4** | Python < 3.12 compatibility fix (nested f-string SyntaxError) |
| **1.0.3** | Configurable port at installation, removed `sudo` from `install.sh` |
| **1.0.1** | Account recovery (OTP email + no-SMTP mode), rate limiting, cookie flags, HTTP security headers, logout button on dashboard |
| **1.0.0** | Initial release — dashboard, SMTP alerts, history, reports, SSL, authentication, GitHub update |

---

## License

MIT — free to use, modify and distribute.
