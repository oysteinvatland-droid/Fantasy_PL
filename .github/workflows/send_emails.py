#!/usr/bin/env python3
"""Sender e-poster til abonnenter - vanlig rapport eller velkomst"""

import json
import smtplib
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Sjekk om dette er velkomst-modus
is_welcome = len(sys.argv) > 1 and sys.argv[1] == '--welcome'

if is_welcome:
    reports_file = 'welcome_reports.json'
    subject = "🎉 Velkommen til din Fantasy Premier League - AI Rapport"
    print("=== VELKOMST-MODUS ===")
else:
    reports_file = 'generated_reports.json'
    gw_number = os.environ.get('GW_NUMBER', '?')
    hours_until = os.environ.get('HOURS_UNTIL', '?')
    subject = f"⚠️ HUSK Å OPPDATERE LAGET! GW{gw_number} - Kun {hours_until} timer igjen!"
    print("=== VANLIG RAPPORT-MODUS ===")

# Les rapporter
try:
    with open(reports_file, 'r') as f:
        reports = json.load(f)
    print(f"Fant {len(reports)} rapporter i {reports_file}")
except FileNotFoundError:
    print(f"Ingen rapporter funnet ({reports_file})")
    exit(0)  # Ikke feil, bare ingen å sende
except Exception as e:
    print(f"Feil ved lesing av rapporter: {e}")
    exit(1)

if len(reports) == 0:
    print("Ingen rapporter å sende")
    exit(0)

# E-post innstillinger
smtp_server = "smtp.gmail.com"
smtp_port = 587
username = os.environ.get('EMAIL_USERNAME')
password = os.environ.get('EMAIL_PASSWORD')

if not username or not password:
    print("Mangler EMAIL_USERNAME eller EMAIL_PASSWORD")
    exit(1)

print(f"Kobler til {smtp_server}...")
server = smtplib.SMTP(smtp_server, smtp_port)
server.starttls()
server.login(username, password)
print("Innlogget!")

sent_count = 0
for report in reports:
    try:
        email = report['email']
        name = report['name']
        report_file = report['report_file']
        
        print(f"Sender til {name} ({email})...")
        print(f"  Emne: {subject}")
        
        with open(report_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = "FPL Analyse <kontakt@fplanalyse.no>"
        msg['To'] = email
        
        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)
        
        server.sendmail(username, email, msg.as_string())
        print(f"  ✓ Sendt!")
        sent_count += 1
        
    except Exception as e:
        print(f"  ✗ Feil: {e}")

server.quit()
print(f"\nFerdig! Sendt {sent_count} e-poster.")

# Hvis velkomst-modus, oppdater kjente abonnenter
if is_welcome and sent_count > 0:
    print("\nOppdaterer liste over kjente abonnenter...")
    
    # Last eksisterende kjente abonnenter
    known_file = 'known_subscribers.json'
    known = set()
    if os.path.exists(known_file):
        try:
            with open(known_file, 'r') as f:
                known = set(json.load(f))
        except:
            pass
    
    # Legg til nye
    for report in reports:
        known.add(report['email'].lower())
    
    # Lagre
    with open(known_file, 'w') as f:
        json.dump(list(known), f, indent=2)
    
    print(f"Oppdatert {known_file} med {len(known)} abonnenter")
