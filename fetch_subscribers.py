#!/usr/bin/env python3
"""Henter abonnenter fra Google Sheets og lagrer til subscribers.json"""

import csv
import json
import requests
from io import StringIO

SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSoJcowM4KinERx4akJDe2HM1cgtvbXPRgcTXMdbWt0FY2OXEJ5K9vGvpuQh5FwtZauSDAcmfEfzKYh/pub?output=csv"

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

subscribers = []
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
        subscribers.append({
            'name': name,
            'email': email,
            'team_id': team_id
        })
        print(f"  Lagt til!")

print(f"\nTotalt {len(subscribers)} abonnenter funnet")

# Lagre til JSON
with open('subscribers.json', 'w', encoding='utf-8') as f:
    json.dump(subscribers, f, indent=2, ensure_ascii=False)

print("Lagret til subscribers.json")
