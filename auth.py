"""
auth.py - À lancer UNE SEULE FOIS pour autoriser l'accès à la Freebox.
L'écran de la Freebox Delta affichera une demande d'autorisation.
"""
import requests
import json
import time
import sys
import os
import crypto

FREEBOX_URL = "http://mafreebox.freebox.fr"
API_VERSION = "v8"
CREDENTIALS_FILE = "credentials.json"

APP_ID = "fr.monitor.freebox"
APP_NAME = "Freebox Monitor"
APP_VERSION = "1.0.0"
DEVICE_NAME = "Mon PC"


def api_url(path=""):
    return f"{FREEBOX_URL}/api/{API_VERSION}{path}"


def authorize():
    print("Connexion à la Freebox Delta...")

    try:
        r = requests.post(api_url("/login/authorize/"), json={
            "app_id": APP_ID,
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "device_name": DEVICE_NAME,
        }, timeout=10)
    except requests.ConnectionError:
        print("\nERREUR : Impossible de joindre mafreebox.freebox.fr")
        print("→ Vérifiez que vous êtes bien sur le réseau local de la Freebox.")
        sys.exit(1)

    data = r.json()
    if not data.get("success"):
        print(f"Erreur API : {data}")
        sys.exit(1)

    result = data["result"]
    app_token = result["app_token"]
    track_id = result["track_id"]

    print("\n" + "="*60)
    print("  Regardez l'écran de votre Freebox Delta et appuyez sur")
    print("  la flèche droite pour AUTORISER l'application.")
    print("="*60 + "\n")

    while True:
        r = requests.get(api_url(f"/login/authorize/{track_id}"), timeout=10)
        status = r.json()["result"]["status"]

        if status == "granted":
            print("✓ Autorisation accordée !")
            break
        elif status == "pending":
            print("  En attente de validation sur la Freebox...")
            time.sleep(3)
        elif status == "denied":
            print("✗ Autorisation refusée sur la Freebox.")
            sys.exit(1)
        elif status == "timeout":
            print("✗ Timeout — relancez auth.py pour réessayer.")
            sys.exit(1)
        else:
            print(f"  Statut: {status}...")
            time.sleep(2)

    data = {"app_id": APP_ID, "app_token": crypto.encrypt(app_token)}
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(CREDENTIALS_FILE, 0o600)

    print(f"✓ Credentials sauvegardés dans {CREDENTIALS_FILE}")
    print("\nVous pouvez maintenant lancer : python monitor.py")


if __name__ == "__main__":
    authorize()
