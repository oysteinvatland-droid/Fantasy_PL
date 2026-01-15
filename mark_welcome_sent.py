#!/usr/bin/env python3
"""Oppdaterer Firebase for å markere at velkomst-e-post er sendt"""

import json
import requests
import sys

PROJECT_ID = "fpl-ai-analyzer"

def mark_welcome_sent(doc_id):
    """Oppdater et dokument i Firestore for å sette welcome_sent = true"""
    url = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents/subscribers/{doc_id}?updateMask.fieldPaths=welcome_sent"
    
    payload = {
        "fields": {
            "welcome_sent": {"booleanValue": True}
        }
    }
    
    response = requests.patch(url, json=payload, timeout=30)
    return response.status_code == 200

# Les rapport-filen for å finne hvem som fikk e-post
try:
    with open('welcome_reports.json', 'r') as f:
        reports = json.load(f)
except FileNotFoundError:
    print("Ingen welcome_reports.json funnet")
    exit(0)

# Les nye abonnenter for å få doc_id
try:
    with open('new_subscribers.json', 'r') as f:
        new_subs = json.load(f)
except FileNotFoundError:
    print("Ingen new_subscribers.json funnet")
    exit(0)

# Lag en mapping fra email til doc_id
email_to_doc = {sub['email'].lower(): sub.get('doc_id') for sub in new_subs}

print(f"Oppdaterer {len(reports)} abonnenter i Firebase...")

for report in reports:
    email = report['email'].lower()
    doc_id = email_to_doc.get(email)
    
    if doc_id:
        success = mark_welcome_sent(doc_id)
        if success:
            print(f"  ✓ {email} markert som welcome_sent")
        else:
            print(f"  ✗ Kunne ikke oppdatere {email}")
    else:
        print(f"  ? Fant ikke doc_id for {email}")

print("Ferdig!")
