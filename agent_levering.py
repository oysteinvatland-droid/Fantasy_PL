#!/usr/bin/env python3
"""
AGENT 4 – LEVERING-AGENT
Ansvar: Sender e-poster og oppdaterer Firebase.
Ingen analyse, ingen rapportgenerering – bare levering.

Input:
  - generated_reports.json / welcome_reports.json
  - reports/*.html
  - Miljøvariabler: EMAIL_USERNAME, EMAIL_PASSWORD, GW_NUMBER, HOURS_UNTIL

Output:
  - Sendte e-poster
  - Oppdatert Firebase (welcome_sent = true)
"""

import os
import sys
import argparse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from pipeline_shared.firestore import mark_welcome_sent
from pipeline_shared.io import read_json


# ─── Hjelpefunksjoner ─────────────────────────────────────────────────────────

def hent_modus_konfig(is_welcome):
    """Returnerer rapportfil og emne for valgt leveringsmodus."""
    if is_welcome:
        return {
            'manifest_file': 'welcome_reports.json',
            'subject': "🎉 Velkommen til din Fantasy Premier League - AI Rapport",
        }

    gw_number = os.environ.get('GW_NUMBER', '?')
    hours_until = os.environ.get('HOURS_UNTIL', '?')
    return {
        'manifest_file': 'generated_reports.json',
        'subject': f"⚠️ HUSK Å OPPDATERE LAGET! GW{gw_number} – Kun {hours_until} timer igjen!",
    }


def send_epost(server, fra, til, emne, html_innhold):
    """Sender én e-post via åpen SMTP-tilkobling"""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = emne
    msg['From'] = fra
    msg['To'] = til
    msg.attach(MIMEText(html_innhold, 'html'))
    server.sendmail(fra.split('<')[-1].rstrip('>'), til, msg.as_string())


def koble_til_smtp():
    """Kobler til Gmail SMTP og returnerer server-objekt"""
    username = os.environ.get('EMAIL_USERNAME')
    password = os.environ.get('EMAIL_PASSWORD')

    if not username or not password:
        print("❌ Mangler EMAIL_USERNAME eller EMAIL_PASSWORD i miljøvariabler")
        sys.exit(1)

    print(f"📨 Kobler til smtp.gmail.com...")
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(username, password)
    print("✓ Innlogget på SMTP")
    return server, username


def send_rapporter(reports, emne, fra="FPL Analyse <kontakt@fplanalyse.no>"):
    """Sender HTML-rapporter til alle i listen"""
    if not reports:
        print("ℹ️ Ingen rapporter å sende")
        return 0, 0

    server, _username = koble_til_smtp()
    sendt = 0
    feil = 0

    try:
        for report in reports:
            email = report.get('email')
            name = report.get('name')
            report_file = report.get('report_file')

            if not email or not report_file:
                print(f"  ⚠️ Mangler email eller report_file for {name}")
                continue

            try:
                with open(report_file, 'r', encoding='utf-8') as f:
                    html = f.read()

                send_epost(server, fra, email, emne, html)
                print(f"  ✓ Sendt til {name} ({email})")
                sendt += 1
            except FileNotFoundError:
                print(f"  ⚠️ Rapport-fil ikke funnet: {report_file}")
                feil += 1
            except Exception as e:
                print(f"  ⚠️ Feil ved sending til {name}: {e}")
                feil += 1
    finally:
        server.quit()
        print(f"✓ SMTP-tilkobling lukket")

    print(f"\n📊 Resultat: {sendt} sendt, {feil} feil")
    return sendt, feil


def oppdater_firebase_welcome_sent(welcome_reports, new_subscribers):
    """Markerer welcome_sent = true i Firebase for nye abonnenter"""
    email_to_doc = {
        sub['email'].lower(): sub.get('doc_id')
        for sub in new_subscribers
    }

    print(f"\n🔥 Oppdaterer Firebase for {len(welcome_reports)} abonnenter...")
    oppdatert = 0

    for report in welcome_reports:
        email = report.get('email', '').lower()
        doc_id = email_to_doc.get(email)

        if not doc_id:
            print(f"  ? Fant ikke doc_id for {email}")
            continue

        try:
            if mark_welcome_sent(doc_id):
                print(f"  ✓ {email} markert som welcome_sent")
                oppdatert += 1
            else:
                print(f"  ✗ Kunne ikke oppdatere {email}")
        except Exception as e:
            print(f"  ✗ Firebase-feil for {email}: {e}")

    return oppdatert


# ─── Hovedprogram ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--welcome', action='store_true',
                        help='Send velkomst-e-poster til nye abonnenter')
    args = parser.parse_args()

    print("=" * 60)
    print("🤖 AGENT 4 – LEVERING-AGENT")
    print(f"   Modus: {'VELKOMST' if args.welcome else 'UKENTLIG'}")
    print("=" * 60)

    config = hent_modus_konfig(is_welcome=args.welcome)
    reports = read_json(config['manifest_file'], default=[])

    if args.welcome:
        if not reports:
            print("ℹ️ Ingen velkomst-rapporter å sende")
            write_json('delivery_status.json', {'mode': 'welcome', 'reports': 0, 'sent': 0, 'failed': 0, 'skipped': True})
            sys.exit(0)

        sendt = send_rapporter(reports, config['subject'])

        # Oppdater Firebase
        if sendt > 0:
            new_subs = read_json('new_subscribers.json', default=[])
            oppdater_firebase_welcome_sent(reports, new_subs)

    else:
        if not reports:
            print("ℹ️ Ingen ukentlige rapporter å sende")
            write_json('delivery_status.json', {'mode': 'weekly', 'reports': 0, 'sent': 0, 'failed': 0, 'skipped': True})
            sys.exit(0)

        sendt = send_rapporter(reports, config['subject'])

    print("\n" + "=" * 60)
    print(f"✅ AGENT 4 FULLFØRT – {sendt} e-poster sendt")
    print("=" * 60)

    if sendt == 0 and reports:
        print("⚠️ Ingen e-poster ble sendt – sjekk feilmeldinger over")
        sys.exit(1)
