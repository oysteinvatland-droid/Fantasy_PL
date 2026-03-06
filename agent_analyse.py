#!/usr/bin/env python3
"""
AGENT 2 – ANALYSE-AGENT
Ansvar: Leser rådata og beregner alle scores, rangeringer og anbefalinger.
Ingen API-kall, ingen rapportgenerering – bare ren dataanalyse.

Input:
  - fpl_raw_data.json

Output:
  - fpl_analysis.json : Alle beregnede scores og rangeringer
"""

import json
import sys
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timezone

warnings.filterwarnings('ignore')


# ─── Last inn rådata ──────────────────────────────────────────────────────────

def last_rawdata(filnavn='fpl_raw_data.json'):
    print(f"📂 Leser {filnavn}...")
    try:
        with open(filnavn, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ Finner ikke {filnavn} – kjør agent_data.py først")
        sys.exit(1)


# ─── Dataklargjøring ──────────────────────────────────────────────────────────

def bygg_dataframes(raw):
    bootstrap = raw['bootstrap']
    spillere = bootstrap['elements']
    lag = {t['id']: t['name'] for t in bootstrap['teams']}
    lag_short = {t['id']: t['short_name'] for t in bootstrap['teams']}
    posisjoner = {p['id']: p['singular_name_short'] for p in bootstrap['element_types']}

    teams_df = pd.DataFrame(bootstrap['teams'])
    teams_df['team_id'] = teams_df['id']

    df = pd.DataFrame(spillere)
    df['lag_navn'] = df['team'].map(lag)
    df['lag_short'] = df['team'].map(lag_short)
    df['posisjon'] = df['element_type'].map(posisjoner)

    fixtures_df = pd.DataFrame(raw['fixtures']) if raw['fixtures'] else pd.DataFrame()

    return df, teams_df, fixtures_df


def beregn_basis_metrics(df):
    df = df.copy()
    df['pris_mill'] = df['now_cost'] / 10
    df['ppm'] = df['total_points'] / df['pris_mill'].replace(0, np.nan)
    df['form_num'] = pd.to_numeric(df['form'], errors='coerce')
    df['ppk'] = df.apply(lambda x: x['total_points'] / x['minutes'] * 90 if x['minutes'] > 0 else 0, axis=1)
    df['valgt_prosent'] = pd.to_numeric(df['selected_by_percent'], errors='coerce')
    df['ict_index_num'] = pd.to_numeric(df['ict_index'], errors='coerce')
    df['influence'] = pd.to_numeric(df['influence'], errors='coerce')
    df['creativity'] = pd.to_numeric(df['creativity'], errors='coerce')
    df['threat'] = pd.to_numeric(df['threat'], errors='coerce')
    df['expected_goals'] = pd.to_numeric(df['expected_goals'], errors='coerce')
    df['expected_assists'] = pd.to_numeric(df['expected_assists'], errors='coerce')
    df['expected_goal_involvements'] = pd.to_numeric(df['expected_goal_involvements'], errors='coerce')
    df['bonus'] = pd.to_numeric(df['bonus'], errors='coerce')
    df['bps'] = pd.to_numeric(df['bps'], errors='coerce')
    df['points_per_game'] = pd.to_numeric(df['points_per_game'], errors='coerce')
    df['clean_sheets'] = pd.to_numeric(df['clean_sheets'], errors='coerce')
    df['goals_conceded'] = pd.to_numeric(df['goals_conceded'], errors='coerce')
    return df


def berik_med_historikk(df, spiller_historikk):
    """Legger til siste-4-kamper-statistikk fra historikk-cache"""
    def hent(player_id):
        data = spiller_historikk.get(str(player_id))
        if data:
            return pd.Series(data)
        return pd.Series({
            'starts_siste_4': 0, 'minutter_siste_4': 0,
            'antall_kamper': 0, 'poeng_siste_4': 0, 'ppg_siste_4': 0
        })

    historikk_cols = df['id'].apply(hent)
    return pd.concat([df, historikk_cols], axis=1)


# ─── Fixture difficulty ───────────────────────────────────────────────────────

def beregn_fixture_difficulty(fixtures_df, team_id, antall=5):
    if fixtures_df.empty:
        return None
    kommende = fixtures_df[
        ((fixtures_df['team_h'] == team_id) | (fixtures_df['team_a'] == team_id)) &
        (fixtures_df['finished'] == False)
    ].head(antall)
    if len(kommende) == 0:
        return None
    total = 0
    for _, k in kommende.iterrows():
        total += k['team_h_difficulty'] if k['team_h'] == team_id else k['team_a_difficulty']
    return total / len(kommende)


def beregn_alle_fixture_difficulties(fixtures_df, teams_df, antall=5):
    return {
        int(row['id']): beregn_fixture_difficulty(fixtures_df, row['id'], antall)
        for _, row in teams_df.iterrows()
    }


# ─── Lagstyrke ───────────────────────────────────────────────────────────────

def beregn_lagstyrke(df, teams_df):
    attack = {}
    defense = {}
    for _, team in teams_df.iterrows():
        tid = team['id']
        team_players = df[df['team'] == tid]
        defenders = team_players[team_players['element_type'] <= 2]

        xg = pd.to_numeric(team_players['expected_goals'], errors='coerce').fillna(0).sum()
        str_att = float((team['strength_attack_home'] + team['strength_attack_away']) / 2)
        str_def = float((team['strength_defence_home'] + team['strength_defence_away']) / 2)
        cs = pd.to_numeric(defenders['clean_sheets'], errors='coerce').fillna(0).sum()

        attack[tid] = {
            'xg_total': float(xg),
            'strength_attack': str_att,
            'combined_attack': float(xg * 0.6 + str_att * 0.4)
        }
        defense[tid] = {
            'clean_sheets': float(cs),
            'strength_defense': str_def,
            'combined_defense': float(cs * 0.5 + (6 - str_def) * 10)
        }
    return attack, defense


# ─── Posisjonsspesifikke scores ───────────────────────────────────────────────

def spiss_score(df, fixture_diff, team_attack, team_defense):
    vekter = {
        'form': 0.20, 'ppk': 0.15, 'xg': 0.20, 'xgi': 0.10,
        'ict': 0.10, 'threat': 0.10, 'ppm': 0.05,
        'fixture': 0.05, 'siste4': 0.05
    }
    sub = df[df['posisjon'] == 'FWD'].copy()
    for col in ['form_num', 'ppk', 'expected_goals', 'expected_goal_involvements',
                'ict_index_num', 'threat', 'ppm']:
        sub[col] = pd.to_numeric(sub[col], errors='coerce').fillna(0)
        max_v = sub[col].max()
        sub[f'{col}_norm'] = (sub[col] / max_v * 100) if max_v > 0 else 0

    sub['fixture_norm'] = sub['team'].apply(
        lambda t: max(0, 100 - (fixture_diff.get(t) or 3) * 20))
    sub['siste4_norm'] = (sub['ppg_siste_4'] / sub['ppg_siste_4'].max() * 100) if sub['ppg_siste_4'].max() > 0 else 0

    sub['score'] = (
        sub['form_num_norm'] * vekter['form'] +
        sub['ppk_norm'] * vekter['ppk'] +
        sub['expected_goals_norm'] * vekter['xg'] +
        sub['expected_goal_involvements_norm'] * vekter['xgi'] +
        sub['ict_index_num_norm'] * vekter['ict'] +
        sub['threat_norm'] * vekter['threat'] +
        sub['ppm_norm'] * vekter['ppm'] +
        sub['fixture_norm'] * vekter['fixture'] +
        sub['siste4_norm'] * vekter['siste4']
    )
    return sub


def midtbane_score(df, fixture_diff, team_attack):
    vekter = {
        'form': 0.18, 'ppk': 0.15, 'xgi': 0.15, 'creativity': 0.12,
        'ict': 0.10, 'ppm': 0.08, 'fixture': 0.07,
        'bonus': 0.07, 'siste4': 0.08
    }
    sub = df[df['posisjon'] == 'MID'].copy()
    for col in ['form_num', 'ppk', 'expected_goal_involvements',
                'creativity', 'ict_index_num', 'ppm', 'bonus']:
        sub[col] = pd.to_numeric(sub[col], errors='coerce').fillna(0)
        max_v = sub[col].max()
        sub[f'{col}_norm'] = (sub[col] / max_v * 100) if max_v > 0 else 0

    sub['fixture_norm'] = sub['team'].apply(
        lambda t: max(0, 100 - (fixture_diff.get(t) or 3) * 20))
    sub['siste4_norm'] = (sub['ppg_siste_4'] / sub['ppg_siste_4'].max() * 100) if sub['ppg_siste_4'].max() > 0 else 0

    sub['score'] = (
        sub['form_num_norm'] * vekter['form'] +
        sub['ppk_norm'] * vekter['ppk'] +
        sub['expected_goal_involvements_norm'] * vekter['xgi'] +
        sub['creativity_norm'] * vekter['creativity'] +
        sub['ict_index_num_norm'] * vekter['ict'] +
        sub['ppm_norm'] * vekter['ppm'] +
        sub['fixture_norm'] * vekter['fixture'] +
        sub['bonus_norm'] * vekter['bonus'] +
        sub['siste4_norm'] * vekter['siste4']
    )
    return sub


def forsvar_score(df, fixture_diff, team_defense):
    vekter = {
        'clean_sheets': 0.20, 'fixture': 0.20, 'ppk': 0.15,
        'defense': 0.15, 'form': 0.10, 'ppm': 0.08,
        'bonus': 0.07, 'siste4': 0.05
    }
    sub = df[df['posisjon'] == 'DEF'].copy()
    for col in ['clean_sheets', 'ppk', 'form_num', 'ppm', 'bonus']:
        sub[col] = pd.to_numeric(sub[col], errors='coerce').fillna(0)
        max_v = sub[col].max()
        sub[f'{col}_norm'] = (sub[col] / max_v * 100) if max_v > 0 else 0

    sub['fixture_norm'] = sub['team'].apply(
        lambda t: max(0, 100 - (fixture_diff.get(t) or 3) * 20))
    max_def = max(team_defense.values(), key=lambda x: x['combined_defense'])['combined_defense'] if team_defense else 1
    sub['defense_norm'] = sub['team'].apply(
        lambda t: (team_defense.get(t, {}).get('combined_defense', 0) / max_def * 100) if max_def > 0 else 0)
    sub['siste4_norm'] = (sub['ppg_siste_4'] / sub['ppg_siste_4'].max() * 100) if sub['ppg_siste_4'].max() > 0 else 0

    sub['score'] = (
        sub['clean_sheets_norm'] * vekter['clean_sheets'] +
        sub['fixture_norm'] * vekter['fixture'] +
        sub['ppk_norm'] * vekter['ppk'] +
        sub['defense_norm'] * vekter['defense'] +
        sub['form_num_norm'] * vekter['form'] +
        sub['ppm_norm'] * vekter['ppm'] +
        sub['bonus_norm'] * vekter['bonus'] +
        sub['siste4_norm'] * vekter['siste4']
    )
    return sub


def keeper_score(df, fixture_diff, team_defense):
    vekter = {
        'clean_sheets': 0.25, 'fixture': 0.20, 'saves': 0.15,
        'ppk': 0.15, 'defense': 0.15, 'ppm': 0.10
    }
    sub = df[df['posisjon'] == 'GKP'].copy()
    sub['saves'] = pd.to_numeric(sub['saves'], errors='coerce').fillna(0)
    for col in ['clean_sheets', 'ppk', 'ppm', 'saves']:
        sub[col] = pd.to_numeric(sub[col], errors='coerce').fillna(0)
        max_v = sub[col].max()
        sub[f'{col}_norm'] = (sub[col] / max_v * 100) if max_v > 0 else 0

    sub['fixture_norm'] = sub['team'].apply(
        lambda t: max(0, 100 - (fixture_diff.get(t) or 3) * 20))
    max_def = max(team_defense.values(), key=lambda x: x['combined_defense'])['combined_defense'] if team_defense else 1
    sub['defense_norm'] = sub['team'].apply(
        lambda t: (team_defense.get(t, {}).get('combined_defense', 0) / max_def * 100) if max_def > 0 else 0)

    sub['score'] = (
        sub['clean_sheets_norm'] * vekter['clean_sheets'] +
        sub['fixture_norm'] * vekter['fixture'] +
        sub['saves_norm'] * vekter['saves'] +
        sub['ppk_norm'] * vekter['ppk'] +
        sub['defense_norm'] * vekter['defense'] +
        sub['ppm_norm'] * vekter['ppm']
    )
    return sub


# ─── Topp-lister ─────────────────────────────────────────────────────────────

def topp_spillere(scored_df, antall=15, min_min=180, score_col='score'):
    return (scored_df[scored_df['minutes'] >= min_min]
            .sort_values(score_col, ascending=False)
            .head(antall)[['id', 'web_name', 'lag_navn', 'lag_short', 'posisjon',
                            'pris_mill', 'total_points', 'form_num', score_col,
                            'starts_siste_4', 'ppg_siste_4']]
            .to_dict('records'))


def value_for_money(df, maks_pris=6.0, min_min=180, antall=10):
    """Rimelige perler – god score relativt til pris"""
    sub = df[(df['pris_mill'] <= maks_pris) & (df['minutes'] >= min_min)].copy()
    sub['vfm'] = pd.to_numeric(sub['total_points'], errors='coerce') / sub['pris_mill']
    return (sub.sort_values('vfm', ascending=False)
            .head(antall)[['id', 'web_name', 'lag_short', 'posisjon', 'pris_mill',
                            'total_points', 'vfm', 'form_num']]
            .to_dict('records'))


def df_to_serializable(records):
    """Konverterer numpy-typer til Python-typer for JSON-serialisering"""
    clean = []
    for r in records:
        clean.append({
            k: (float(v) if isinstance(v, (np.floating, np.integer)) else
                int(v) if isinstance(v, np.integer) else v)
            for k, v in r.items()
        })
    return clean


# ─── Hovedprogram ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 AGENT 2 – ANALYSE-AGENT")
    print("=" * 60)

    raw = last_rawdata()

    # Bygg DataFrames
    df, teams_df, fixtures_df = bygg_dataframes(raw)
    df = beregn_basis_metrics(df)
    df = berik_med_historikk(df, raw.get('spiller_historikk', {}))
    print(f"✓ {len(df)} spillere lastet inn")

    # Beregn lagstyrke og fixture difficulty
    fixture_diff = beregn_alle_fixture_difficulties(fixtures_df, teams_df)
    team_attack, team_defense = beregn_lagstyrke(df, teams_df)
    print("✓ Lagstyrke og fixture difficulty beregnet")

    # Score per posisjon
    fwd_df = spiss_score(df, fixture_diff, team_attack, team_defense)
    mid_df = midtbane_score(df, fixture_diff, team_attack)
    def_df = forsvar_score(df, fixture_diff, team_defense)
    gkp_df = keeper_score(df, fixture_diff, team_defense)
    print("✓ Posisjonsspesifikke scores beregnet")

    # Topp-lister
    analyse = {
        'beregnet_tidspunkt': datetime.now(timezone.utc).isoformat(),
        'topp_spisser': df_to_serializable(topp_spillere(fwd_df, 15)),
        'topp_midtbane': df_to_serializable(topp_spillere(mid_df, 15)),
        'topp_forsvar': df_to_serializable(topp_spillere(def_df, 15)),
        'topp_keepere': df_to_serializable(topp_spillere(gkp_df, 10)),
        'value_for_money': df_to_serializable(value_for_money(df, maks_pris=6.0)),
        'fixture_difficulty': {str(k): v for k, v in fixture_diff.items()},
        'team_attack': {str(k): v for k, v in team_attack.items()},
        'team_defense': {str(k): v for k, v in team_defense.items()},
        'alle_spillere_scored': df_to_serializable(
            pd.concat([fwd_df, mid_df, def_df, gkp_df])
            [['id', 'web_name', 'lag_navn', 'lag_short', 'posisjon',
              'pris_mill', 'total_points', 'form_num', 'score',
              'starts_siste_4', 'ppg_siste_4', 'minutes', 'valgt_prosent']]
            .to_dict('records')
        )
    }

    with open('fpl_analysis.json', 'w', encoding='utf-8') as f:
        json.dump(analyse, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("✅ AGENT 2 FULLFØRT")
    print(f"   Topp spisser    : {len(analyse['topp_spisser'])}")
    print(f"   Topp midtbane   : {len(analyse['topp_midtbane'])}")
    print(f"   Topp forsvar    : {len(analyse['topp_forsvar'])}")
    print(f"   Topp keepere    : {len(analyse['topp_keepere'])}")
    print(f"   Alle scoret     : {len(analyse['alle_spillere_scored'])}")
    print("=" * 60)
