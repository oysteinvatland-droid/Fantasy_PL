#!/usr/bin/env python3
"""Henter abonnenter fra Firebase Firestore og identifiserer nye abonnenter"""

import json
import requests
import os

# Firebase Firestore REST API
PROJECT_ID = "fpl-ai-analyzer"
FIRESTORE_URL = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents/subscribers"

print(f"Henter abonnenter fra Firebase Firestore...")
print(f"URL: {FIRESTORE_URL}")

try:
    response = requests.get(FIRESTORE_URL, timeout=30)
    print(f"Status: {response.status_code}")
    
    if response.status_code != 200:
        print(f"Feil ved henting: {response.text}")
        # Lag tom liste hvis ingen data
        with open('subscribers.json', 'w') as f:
            json.dump([], f)
        with open('new_subscribers.json', 'w') as f:
            json.dump([], f)
        with open('has_new_subscribers.txt', 'w') as f:
            f.write('false')
        exit(0)
    
    data = response.json()
    
except Exception as e:
    print(f"Feil ved API-kall: {e}")
    with open('subscribers.json', 'w') as f:
        json.dump([], f)
    with open('new_subscribers.json', 'w') as f:
        json.dump([], f)
    with open('has_new_subscribers.txt', 'w') as f:
        f.write('false')
    exit(0)

# Parse Firestore response
subscribers = []
new_subscribers = []

documents = data.get('documents', [])
print(f"Fant {len(documents)} dokumenter i Firestore")

for doc in documents:
    fields = doc.get('fields', {})
    
    # Extract document ID for later update
    doc_path = doc.get('name', '')
    doc_id = doc_path.split('/')[-1] if doc_path else None
    
    # Parse fields (Firestore returns typed values)
    name = fields.get('name', {}).get('stringValue', '')
    email = fields.get('email', {}).get('stringValue', '')
    
    # team_id kan være integerValue eller stringValue
    team_id_field = fields.get('team_id', {})
    if 'integerValue' in team_id_field:
        team_id = int(team_id_field['integerValue'])
    elif 'stringValue' in team_id_field:
        try:
            team_id = int(team_id_field['stringValue'])
        except:
            team_id = 0
    else:
        team_id = 0
    
    welcome_sent = fields.get('welcome_sent', {}).get('booleanValue', False)
    
    print(f"  Abonnent: {name} ({email}) - Team ID: {team_id} - Welcome sent: {welcome_sent}")
    
    if name and email and team_id > 0:
        subscriber = {
            'name': name,
            'email': email,
            'team_id': team_id,
            'doc_id': doc_id
        }
        subscribers.append(subscriber)
        
        # Sjekk om velkomst-e-post ikke er sendt enda
        if not welcome_sent:
            new_subscribers.append(subscriber)
            print(f"    ✨ NY ABONNENT (velkomst ikke sendt)")

print(f"\nTotalt {len(subscribers)} abonnenter")
print(f"Nye abonnenter (venter på velkomst): {len(new_subscribers)}")

# Lagre alle abonnenter
with open('subscribers.json', 'w', encoding='utf-8') as f:
    json.dump(subscribers, f, indent=2, ensure_ascii=False)
print("Lagret subscribers.json")

# Lagre nye abonnenter separat
with open('new_subscribers.json', 'w', encoding='utf-8') as f:
    json.dump(new_subscribers, f, indent=2, ensure_ascii=False)
print("Lagret new_subscribers.json")

# Skriv ut om vi har nye abonnenter
if new_subscribers:
    print("HAS_NEW_SUBSCRIBERS=true")
    with open('has_new_subscribers.txt', 'w') as f:
        f.write('true')
else:
    print("HAS_NEW_SUBSCRIBERS=false")
    with open('has_new_subscribers.txt', 'w') as f:
        f.write('false')
