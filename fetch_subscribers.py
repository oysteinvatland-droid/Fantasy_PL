#!/usr/bin/env python3
"""Henter abonnenter fra Google Sheets og identifiserer nye abonnenter"""

import csv
import json
import requests
import os
from io import StringIO

SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSoJcowM4KinERx4akJDe2HM1cgtvbXPRgcTXMdbWt0FY2OXEJ5K9vGvpuQh5FwtZauSDAcmfEfzKYh/pub?output=csv"
KNOWN_SUBSCRIBERS_FILE = "known_subscribers.json"

print(f"Henter fra: {SHEET_URL}")
response = requests.get(SHEET_URL, timeout=30)
print(f"Status: {response.status_code}")
response.raise_for_status()

print(f"CSV data mottatt ({len(response.text)} bytes)")
print("Første 500 tegn:")
print(response.text[:500])
print("---")

# Parse CSV
csv_data = StringIO(response.text)
reader = csv.DictReader(csv_data)

# Vis kolonnenavn
rows = list(reader)
if rows:
    print(f"Kolonner funnet: {list(rows[0].keys())}")

# Last inn kjente abonnenter (de som allerede har fått rapport)
known_subscribers = set()
if os.path.exists(KNOWN_SUBSCRIBERS_FILE):
    try:
        with open(KNOWN_SUBSCRIBERS_FILE, 'r') as f:
            known_subscribers = set(json.load(f))
        print(f"Lastet {len(known_subscribers)} kjente abonnenter")
    except:
        print("Kunne ikke laste kjente abonnenter, starter på nytt")

subscribers = []
new_subscribers = []

for row in rows:
    print(f"Rad: {row}")
    
    # Google Forms kolonner - prøv flere varianter
    name = (row.get('Navn') or row.get('Name') or row.get('navn') or '').strip()
    email = (row.get('E-post') or row.get('Email') or row.get('E-mail') or row.get('e-post') or '').strip()
    team_id_str = (row.get('FPL Team ID') or row.get('Team ID') or row.get('Lag-id') or row.get('team_id') or row.get('Lag-ID') or '').strip()
    
    print(f"  Parsed: name={name}, email={email}, team_id_str={team_id_str}")
    
    try:
        team_id = int(team_id_str)
    except (ValueError, TypeError):
        print(f"  Ugyldig team_id: {team_id_str}")
        continue
    
    if name and email and team_id:
        subscriber = {
            'name': name,
            'email': email,
            'team_id': team_id
        }
        subscribers.append(subscriber)
        
        # Sjekk om dette er en ny abonnent (basert på e-post)
        if email.lower() not in known_subscribers:
            new_subscribers.append(subscriber)
            print(f"  ✨ NY ABONNENT!")
        else:
            print(f"  ✓ Kjent abonnent")

print(f"\nTotalt {len(subscribers)} abonnenter funnet")
print(f"Nye abonnenter: {len(new_subscribers)}")

# Lagre alle abonnenter
with open('subscribers.json', 'w', encoding='utf-8') as f:
    json.dump(subscribers, f, indent=2, ensure_ascii=False)
print("Lagret subscribers.json")

# Lagre nye abonnenter separat
with open('new_subscribers.json', 'w', encoding='utf-8') as f:
    json.dump(new_subscribers, f, indent=2, ensure_ascii=False)
print("Lagret new_subscribers.json")

# Skriv ut om vi har nye abonnenter (for workflow)
if new_subscribers:
    print("HAS_NEW_SUBSCRIBERS=true")
    with open('has_new_subscribers.txt', 'w') as f:
        f.write('true')
else:
    print("HAS_NEW_SUBSCRIBERS=false")
    with open('has_new_subscribers.txt', 'w') as f:
        f.write('false')
