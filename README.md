# Freebox Monitor

Dashboard de monitoring temps réel pour routeurs Freebox (Delta, Pop, Ultra, Révolution...).
Déployé en tant que service systemd sur un serveur Linux, accessible depuis n'importe quel navigateur via HTTPS.

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
- Thème clair et sombre avec persistance de la préférence

### Mises à jour
- Vérification de la dernière version depuis GitHub
- Application de la mise à jour en un clic (redémarrage automatique du service)
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

Accédez à **Paramètres** → onglet **Mise à jour** :

**Mise à jour vers la dernière version :**
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

### Port d'écoute

Le port est choisi lors de l'installation (défaut : `8000`). Pour le modifier après installation :

1. Modifiez la valeur dans **Paramètres** → `github_repo` n'est pas le bon endroit — éditez directement la base :

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
├── db.py                    # Couche d'accès SQLite (métriques, config, users, codes OTP)
├── alerts.py                # Envoi d'alertes SMTP (coupure, rétablissement, OTP, test)
├── updater.py               # Vérification et application des mises à jour GitHub
├── auth.py                  # Autorisation Freebox (one-shot, génère credentials.json)
├── bootstrap.sh             # Script d'installation one-liner (curl)
├── install.sh               # Script d'installation complet
├── config.json              # Valeurs par défaut de la configuration
├── requirements.txt         # Dépendances Python (flask, requests)
├── VERSION                  # Version courante du logiciel
├── templates/
│   ├── index.html           # Dashboard principal
│   ├── settings.html        # Page paramètres (SMTP, Mise à jour, Compte)
│   ├── login.html           # Page de connexion
│   ├── setup.html           # Création du compte initial
│   ├── forgot_password.html # Demande de réinitialisation de mot de passe
│   └── reset_password.html  # Formulaire de réinitialisation
├── data/                    # Base SQLite freebox.db — gitignored
├── certs/                   # Certificats SSL (cert.pem, key.pem) — gitignored
└── credentials.json         # Token d'authentification Freebox — gitignored
```

---

## Architecture technique

| Composant | Technologie |
|---|---|
| Backend | Python 3.10+ / Flask 3.0 |
| Base de données | SQLite 3 (via module `sqlite3`) |
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
| `outages` | Journal des coupures (début, fin, durée, cause) |
| `config` | Configuration clé/valeur (SMTP, GitHub, port, secret Flask…) |
| `users` | Compte administrateur (username, hash pbkdf2, email de récupération) |
| `reset_codes` | Codes OTP de réinitialisation (username, code, expiration, utilisé) |

**Rétention des données :** nettoyage automatique hebdomadaire, données conservées 1 an.

---

## Sécurité

| Mesure | Détail |
|---|---|
| Hachage des mots de passe | Werkzeug pbkdf2-sha256 avec sel aléatoire |
| Cookies de session | `Secure`, `HttpOnly`, `SameSite=Lax` |
| En-têtes HTTP | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` |
| Rate limiting | 5 tentatives / 5 min sur `/login` — 3 tentatives / 10 min sur `/forgot-password` |
| Codes OTP | Générés avec `secrets.randbelow()` (CSPRNG), expiration 15 min, usage unique |
| Flux de récupération | Vérification d'identité stockée en session Flask signée côté serveur, aucun paramètre forgeable |
| Permissions fichiers | `credentials.json` et `freebox.db` en `600` — répertoires `certs/` et `data/` en `700` |
| Secrets dans l'interface | SMTP password et GitHub token masqués (`••••••••`) dans les réponses API |
| Injection SQL | Requêtes paramétrées sur toutes les requêtes SQLite |
| XSS | Jinja2 avec auto-escape activé sur tous les templates |
| Transport | HTTPS avec certificat SSL, HTTP désactivé si certificat présent |

---

## Désinstallation

```bash
# Arrêter et désactiver le service
systemctl stop freebox-monitor
systemctl disable freebox-monitor

# Supprimer le fichier de service systemd
rm /etc/systemd/system/freebox-monitor.service
systemctl daemon-reload

# Supprimer le répertoire du projet (données incluses)
rm -rf /root/freebox-monitor
```

> Cette opération supprime définitivement toutes les données : historique des métriques, journal des coupures, configuration, credentials Freebox et certificats SSL.

---

## Historique des versions

| Version | Points clés |
|---|---|
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
