#!/usr/bin/env python3
"""
AGENT 3 – RAPPORT-AGENT
Ansvar: Genererer personlige HTML-rapporter for hver abonnent.
Ingen API-kall, ingen e-postutsending – bare rapportgenerering.

Gjenbruker HTML-logikken fra fpl_analyzer.py, men leser
ferdig-analysert data fra fpl_analysis.json istedenfor å
kjøre analysen på nytt.

Input:
  - fpl_analysis.json
  - fpl_raw_data.json      (for personlig lag-seksjon)
  - subscribers.json / new_subscribers.json

Output:
  - reports/*.html
  - generated_reports.json
  - welcome_reports.json   (hvis --welcome)
"""

import json
import os
import sys
import argparse
from datetime import datetime, timezone

# Importer eksisterende analyzer for å gjenbruke HTML-generering
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Legg til prosjektrot slik at fpl_analyzer kan importeres
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def last_json(filnavn, default=None):
    try:
        with open(filnavn, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️ Finner ikke {filnavn}")
        return default


def initialiser_analyzer():
    """
    Importerer og initialiserer FPLAnalyzer med data fra JSON-filer
    istedenfor å kalle FPL API på nytt.
    """
    try:
        from fpl_analyzer import FPLAnalyzer
    except ImportError:
        print("❌ Kunne ikke importere FPLAnalyzer – sjekk at fpl_analyzer.py finnes i samme mappe")
        sys.exit(1)

    raw = last_json('fpl_raw_data.json')
    if not raw:
        print("❌ Mangler fpl_raw_data.json – kjør agent_data.py først")
        sys.exit(1)

    import pandas as pd

    analyzer = FPLAnalyzer()

    # Injiser rådata direkte uten nye API-kall
    analyzer.data = raw['bootstrap']
    analyzer.fixtures = pd.DataFrame(raw['fixtures']) if raw['fixtures'] else None

    # Bygg spillerdataframe
    analyzer.lag_spillerdataframe()

    # Injiser historikk-cache
    historikk = raw.get('spiller_historikk', {})
    analyzer._player_stats_cache = {int(k): v for k, v in historikk.items()}

    print(f"✓ FPLAnalyzer initialisert med {len(analyzer.players_df)} spillere")
    print(f"✓ Historikk-cache: {len(analyzer._player_stats_cache)} spillere")

    return analyzer


def generer_rapporter(subscribers, analyzer, output_dir='reports', modus='weekly'):
    """Genererer HTML-rapporter for alle abonnenter"""
    os.makedirs(output_dir, exist_ok=True)
    generated = []

    print(f"\n📧 Genererer {modus}-rapporter for {len(subscribers)} abonnenter...")

    for sub in subscribers:
        name = sub.get('name', 'Unknown')
        email = sub.get('email', '')
        team_id = sub.get('team_id', 0)

        if not team_id or not email:
            print(f"  ⚠️ Mangler team_id eller email for {name}")
            continue

        try:
            filnavn = analyzer.generer_rapport_for_abonnent(team_id, name, output_dir=output_dir)
            generated.append({
                'name': name,
                'email': email,
                'team_id': team_id,
                'report_file': filnavn
            })
            print(f"  ✓ {name} → {filnavn}")
        except Exception as e:
            print(f"  ⚠️ Feil ved generering for {name}: {e}")
            import traceback
            traceback.print_exc()

    return generated


# ─── Hovedprogram ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--welcome', action='store_true',
                        help='Generer velkomst-rapporter for nye abonnenter')
    args = parser.parse_args()

    print("=" * 60)
    print("🤖 AGENT 3 – RAPPORT-AGENT")
    print(f"   Modus: {'VELKOMST' if args.welcome else 'UKENTLIG'}")
    print("=" * 60)

    # Initialiser analyzer med cachedt data
    analyzer = initialiser_analyzer()

    if args.welcome:
        # Velkomst-modus: kun nye abonnenter
        subscribers = last_json('new_subscribers.json', default=[])
        if not subscribers:
            print("ℹ️ Ingen nye abonnenter å sende velkomst til")
            with open('welcome_reports.json', 'w') as f:
                json.dump([], f)
            sys.exit(0)

        generated = generer_rapporter(subscribers, analyzer, modus='velkomst')

        with open('welcome_reports.json', 'w', encoding='utf-8') as f:
            json.dump(generated, f, indent=2, ensure_ascii=False)

        print(f"\n💾 Lagret welcome_reports.json ({len(generated)} rapporter)")

    else:
        # Ukentlig modus: alle abonnenter
        subscribers = last_json('subscribers.json', default=[])
        if not subscribers:
            print("⚠️ Ingen abonnenter funnet i subscribers.json")
            with open('generated_reports.json', 'w') as f:
                json.dump([], f)
            sys.exit(0)

        generated = generer_rapporter(subscribers, analyzer, modus='ukentlig')

        with open('generated_reports.json', 'w', encoding='utf-8') as f:
            json.dump(generated, f, indent=2, ensure_ascii=False)

        print(f"\n💾 Lagret generated_reports.json ({len(generated)} rapporter)")

    print("\n" + "=" * 60)
    print(f"✅ AGENT 3 FULLFØRT – {len(generated)} rapporter generert")
    print("=" * 60)
