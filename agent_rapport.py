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

import os
import sys
import argparse

from pipeline_shared.io import read_json, write_json

# Importer eksisterende analyzer for å gjenbruke HTML-generering
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def hent_modus_konfig(is_welcome):
    """Returnerer input-/outputfiler for valgt rapportmodus."""
    if is_welcome:
        return {
            'subscriber_file': 'new_subscribers.json',
            'report_manifest': 'welcome_reports.json',
            'modus_navn': 'velkomst',
        }
    return {
        'subscriber_file': 'subscribers.json',
        'report_manifest': 'generated_reports.json',
        'modus_navn': 'ukentlig',
    }


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

    raw = read_json('fpl_raw_data.json')
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
        config = hent_modus_konfig(is_welcome=True)
        subscribers = read_json(config['subscriber_file'], default=[])
        if not subscribers:
            print("ℹ️ Ingen nye abonnenter å sende velkomst til")
            write_json(config['report_manifest'], [])
            sys.exit(0)

        generated = generer_rapporter(subscribers, analyzer, modus=config['modus_navn'])
        write_json(config['report_manifest'], generated)
        print(f"\n💾 Lagret {config['report_manifest']} ({len(generated)} rapporter)")

    else:
        config = hent_modus_konfig(is_welcome=False)
        subscribers = read_json(config['subscriber_file'], default=[])
        if not subscribers:
            print("⚠️ Ingen abonnenter funnet i subscribers.json")
            write_json(config['report_manifest'], [])
            sys.exit(0)

        generated = generer_rapporter(subscribers, analyzer, modus=config['modus_navn'])
        write_json(config['report_manifest'], generated)
        print(f"\n💾 Lagret {config['report_manifest']} ({len(generated)} rapporter)")

    print("\n" + "=" * 60)
    print(f"✅ AGENT 3 FULLFØRT – {len(generated)} rapporter generert")
    print("=" * 60)
