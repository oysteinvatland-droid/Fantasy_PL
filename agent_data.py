#!/usr/bin/env python3
"""
AGENT 1 - DATA-AGENT
Ansvar: Henter all rådata fra FPL API og Firebase.
Ingen analyse, ingen rapport - bare innhenting og lagring.

Output:
  - fpl_raw_data.json     : All rådata fra FPL API
  - subscribers.json      : Alle aktive abonnenter
  - new_subscribers.json  : Nye abonnenter som ikke har fått velkomst-e-post
  - has_new_subscribers.txt
  - deadline_status.json  : Info om neste gameweek og deadline
"""

import json
import os
import sys
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://fantasy.premierleague.com/api/"
PROJECT_ID = "fpl-ai-analyzer"
FIRESTORE_URL = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents/subscribers"

# ─── Hjelpefunksjoner ────────────────────────────────────────────────────────

def hent_fpl_bootstrap():
    """Henter hoveddata fra FPL API (spillere, lag, gameweeks)"""
    print("📡 Henter FPL bootstrap-data...")
    try:
        response = requests.get(f"{BASE_URL}bootstrap-static/", timeout=10)
        response.raise_for_status()
        print("✓ Bootstrap-data hentet")
        return response.json()
    except requests.exceptions.SSLError:
        print("⚠️ SSL-feil – prøver uten verifisering...")
        response = requests.get(f"{BASE_URL}bootstrap-static/", verify=False, timeout=10)
        response.raise_for_status()
        print("✓ Bootstrap-data hentet (uten SSL)")
        return response.json()


def hent_fixtures():
    """Henter kampprogram"""
    print("📡 Henter fixtures...")
    try:
        response = requests.get(f"{BASE_URL}fixtures/", verify=False, timeout=10)
        response.raise_for_status()
        print(f"✓ {len(response.json())} fixtures hentet")
        return response.json()
    except Exception as e:
        print(f"⚠️ Kunne ikke hente fixtures: {e}")
        return []


def hent_spiller_historikk(player_id):
    """Henter historikk for én spiller"""
    try:
        url = f"{BASE_URL}element-summary/{player_id}/"
        response = requests.get(url, verify=False, timeout=5)
        if response.status_code != 200:
            return player_id, None
        data = response.json()
        history = data.get('history', [])
        if not history:
            return player_id, None
        siste_4 = history[-4:] if len(history) >= 4 else history
        starts = sum(1 for g in siste_4 if g.get('minutes', 0) >= 60)
        total_minutes = sum(g.get('minutes', 0) for g in siste_4)
        total_points = sum(g.get('total_points', 0) for g in siste_4)
        games_played = sum(1 for g in siste_4 if g.get('minutes', 0) > 0)
        ppg = total_points / games_played if games_played > 0 else 0
        return player_id, {
            'starts_siste_4': starts,
            'minutter_siste_4': total_minutes,
            'antall_kamper': len(siste_4),
            'poeng_siste_4': total_points,
            'ppg_siste_4': ppg
        }
    except Exception:
        return player_id, None


def hent_spiller_historikk_batch(player_ids, max_workers=15):
    """Henter historikk for mange spillere parallelt"""
    print(f"📡 Henter historikk for {len(player_ids)} spillere (parallelt, {max_workers} workers)...")
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(hent_spiller_historikk, pid): pid for pid in player_ids}
        ferdig = 0
        for future in as_completed(futures):
            pid, stats = future.result()
            if stats:
                results[str(pid)] = stats
            ferdig += 1
            if ferdig % 100 == 0:
                print(f"  {ferdig}/{len(player_ids)} spillere hentet...")
    print(f"✓ Historikk hentet for {len(results)} spillere")
    return results


def hent_abonnenter():
    """Henter abonnenter fra Firebase Firestore"""
    print("📡 Henter abonnenter fra Firebase...")
    try:
        response = requests.get(FIRESTORE_URL, timeout=30)
        if response.status_code != 200:
            print(f"⚠️ Firebase returnerte {response.status_code}")
            return [], []
        data = response.json()
    except Exception as e:
        print(f"⚠️ Firebase-feil: {e}")
        return [], []

    subscribers = []
    new_subscribers = []

    for doc in data.get('documents', []):
        fields = doc.get('fields', {})
        doc_id = doc.get('name', '').split('/')[-1]

        name = fields.get('name', {}).get('stringValue', '')
        email = fields.get('email', {}).get('stringValue', '')
        welcome_sent = fields.get('welcome_sent', {}).get('booleanValue', False)

        team_id_field = fields.get('team_id', {})
        if 'integerValue' in team_id_field:
            team_id = int(team_id_field['integerValue'])
        elif 'stringValue' in team_id_field:
            try:
                team_id = int(team_id_field['stringValue'])
            except ValueError:
                team_id = 0
        else:
            team_id = 0

        if name and email and team_id > 0:
            sub = {'name': name, 'email': email, 'team_id': team_id, 'doc_id': doc_id}
            subscribers.append(sub)
            if not welcome_sent:
                new_subscribers.append(sub)
                print(f"  ✨ Ny abonnent: {name}")
            else:
                print(f"  ✓ {name} ({email})")

    print(f"✓ {len(subscribers)} abonnenter totalt, {len(new_subscribers)} nye")
    return subscribers, new_subscribers


def sjekk_deadline(bootstrap_data, force_send=False):
    """Sjekker om vi er i utsendingsvinduet (23-24 timer til deadline)"""
    events = bootstrap_data.get('events', [])
    neste_gw = next((e for e in events if not e.get('finished', True)), None)

    if not neste_gw:
        print("⚠️ Ingen kommende gameweek funnet")
        return {'send_weekly': False, 'gw_number': None, 'hours_until': None}

    deadline_str = neste_gw.get('deadline_time', '')
    deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    hours_until = (deadline - now).total_seconds() / 3600
    gw_number = neste_gw.get('id', 0)

    print(f"📅 Neste deadline: {deadline} (GW{gw_number})")
    print(f"⏰ Timer til deadline: {hours_until:.1f}")

    if force_send:
        print("🔧 FORCE SEND aktivert")
        send = True
    elif 23 <= hours_until <= 24:
        print("✅ I utsendingsvinduet (23-24 timer)")
        send = True
    else:
        print(f"⏸️ Utenfor utsendingsvinduet – sender ikke")
        send = False

    return {
        'send_weekly': send,
        'gw_number': gw_number,
        'hours_until': round(hours_until, 1),
        'deadline': deadline_str
    }


# ─── Hovedprogram ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    force_send = os.environ.get('FORCE_SEND', 'false').lower() == 'true'
    print("=" * 60)
    print("🤖 AGENT 1 – DATA-AGENT")
    print("=" * 60)

    # 1. Hent abonnenter fra Firebase
    subscribers, new_subscribers = hent_abonnenter()

    with open('subscribers.json', 'w', encoding='utf-8') as f:
        json.dump(subscribers, f, indent=2, ensure_ascii=False)
    with open('new_subscribers.json', 'w', encoding='utf-8') as f:
        json.dump(new_subscribers, f, indent=2, ensure_ascii=False)
    with open('has_new_subscribers.txt', 'w') as f:
        f.write('true' if new_subscribers else 'false')

    # 2. Hent FPL-data
    bootstrap = hent_fpl_bootstrap()
    fixtures = hent_fixtures()

    # 3. Sjekk deadline
    deadline_status = sjekk_deadline(bootstrap, force_send=force_send)

    with open('deadline_status.json', 'w') as f:
        json.dump(deadline_status, f, indent=2)

    # 4. Hent spillerhistorikk for alle spillere
    player_ids = [p['id'] for p in bootstrap.get('elements', [])]
    spiller_historikk = hent_spiller_historikk_batch(player_ids)

    # 5. Samle alt rådata i én fil
    raw_data = {
        'bootstrap': bootstrap,
        'fixtures': fixtures,
        'spiller_historikk': spiller_historikk,
        'hentet_tidspunkt': datetime.now(timezone.utc).isoformat()
    }

    with open('fpl_raw_data.json', 'w', encoding='utf-8') as f:
        json.dump(raw_data, f, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("✅ AGENT 1 FULLFØRT")
    print(f"   subscribers.json       : {len(subscribers)} abonnenter")
    print(f"   new_subscribers.json   : {len(new_subscribers)} nye")
    print(f"   fpl_raw_data.json      : {len(player_ids)} spillere, {len(fixtures)} fixtures")
    print(f"   send_weekly            : {deadline_status['send_weekly']}")
    print("=" * 60)

    # Sett exit-kode basert på om noe kritisk feilet
    if not bootstrap:
        print("❌ Ingen bootstrap-data – avbryter")
        sys.exit(1)
