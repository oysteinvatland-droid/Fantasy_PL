import requests
import pandas as pd
from datetime import datetime, timedelta
import numpy as np
import warnings
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

# Deaktiver SSL-advarsler (FPL API har noen ganger sertifikat-problemer)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

class FPLAnalyzer:
    def __init__(self):
        self.base_url = "https://fantasy.premierleague.com/api/"
        self.data = None
        self.players_df = None
        self.fixtures = None
        self.teams_df = None
        self._player_stats_cache = {}  # Cache for spillerstatistikk
        
    def hent_siste_4_kamper_stats(self, player_id):
        """Henter spilletidsstatistikk for siste 4 kamper med caching"""
        # Sjekk cache først
        if player_id in self._player_stats_cache:
            return self._player_stats_cache[player_id]
        
        try:
            url = f"{self.base_url}element-summary/{player_id}/"
            response = requests.get(url, verify=False, timeout=5)
            if response.status_code != 200:
                return None
            
            data = response.json()
            history = data.get('history', [])
            
            if len(history) == 0:
                return None
            
            # Ta siste 4 kamper
            siste_4 = history[-4:] if len(history) >= 4 else history
            
            starts = sum(1 for game in siste_4 if game.get('minutes', 0) >= 60)
            total_minutes = sum(game.get('minutes', 0) for game in siste_4)
            total_points = sum(game.get('total_points', 0) for game in siste_4)
            
            # Beregn poeng per kamp (form) for siste 4
            games_with_minutes = sum(1 for game in siste_4 if game.get('minutes', 0) > 0)
            ppg_siste_4 = total_points / games_with_minutes if games_with_minutes > 0 else 0
            
            result = {
                'starts_siste_4': starts,
                'minutter_siste_4': total_minutes,
                'antall_kamper': len(siste_4),
                'poeng_siste_4': total_points,
                'ppg_siste_4': ppg_siste_4
            }
            
            # Lagre i cache
            self._player_stats_cache[player_id] = result
            return result
            
        except Exception:
            return None
    
    def hent_siste_4_kamper_batch(self, player_ids, max_workers=10):
        """Henter spilletidsstatistikk for flere spillere parallelt"""
        results = {}
        
        # Filtrer ut spillere som allerede er i cache
        ids_to_fetch = [pid for pid in player_ids if pid not in self._player_stats_cache]
        
        # Hent fra cache først
        for pid in player_ids:
            if pid in self._player_stats_cache:
                results[pid] = self._player_stats_cache[pid]
        
        if not ids_to_fetch:
            return results
        
        print(f"Henter data for {len(ids_to_fetch)} spillere (parallelt)...")
        
        def fetch_single(player_id):
            return player_id, self.hent_siste_4_kamper_stats(player_id)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_single, pid): pid for pid in ids_to_fetch}
            for future in as_completed(futures):
                try:
                    player_id, stats = future.result()
                    if stats:
                        results[player_id] = stats
                except Exception:
                    pass
        
        return results
    
    def _get_team_games_played(self, team_id):
        """Henter antall kamper et lag har spilt"""
        if self.fixtures is None:
            return 20  # Default
        
        finished_games = self.fixtures[
            ((self.fixtures['team_h'] == team_id) | (self.fixtures['team_a'] == team_id)) &
            (self.fixtures['finished'] == True)
        ]
        return len(finished_games) if len(finished_games) > 0 else 20
        
    def hent_data(self):
        """Henter all FPL data fra API"""
        try:
            # Prøv først med SSL-verifisering
            response = requests.get(f"{self.base_url}bootstrap-static/", timeout=10)
            response.raise_for_status()
            self.data = response.json()
            print("✓ Data hentet fra FPL API")
            return True
        except requests.exceptions.SSLError:
            # Hvis SSL feiler, prøv uten verifisering
            print("⚠️ SSL-feil oppdaget. Prøver uten sertifikatverifisering...")
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                response = requests.get(f"{self.base_url}bootstrap-static/", verify=False, timeout=10)
                response.raise_for_status()
                self.data = response.json()
                print("✓ Data hentet fra FPL API (uten SSL-verifisering)")
                return True
            except Exception as e:
                print(f"Feil ved henting av data: {e}")
                return False
        except Exception as e:
            print(f"Feil ved henting av data: {e}")
            return False
    
    def hent_fixtures(self):
        """Henter fixture data"""
        try:
            response = requests.get(f"{self.base_url}fixtures/", verify=False, timeout=10)
            response.raise_for_status()
            self.fixtures = pd.DataFrame(response.json())
            print("✓ Fixture data hentet")
            return True
        except Exception as e:
            print(f"⚠️ Kunne ikke hente fixtures: {e}")
            return False
    
    def lag_spillerdataframe(self):
        """Lager en pandas DataFrame med spillerdata"""
        if not self.data:
            print("Ingen data tilgjengelig. Kjør hent_data() først.")
            return None
        
        spillere = self.data['elements']
        lag = {team['id']: team['name'] for team in self.data['teams']}
        lag_short = {team['id']: team['short_name'] for team in self.data['teams']}
        posisjoner = {pos['id']: pos['singular_name_short'] for pos in self.data['element_types']}
        
        # Lag også team DataFrame med styrke-ratings
        self.teams_df = pd.DataFrame(self.data['teams'])
        self.teams_df['team_id'] = self.teams_df['id']
        
        df = pd.DataFrame(spillere)
        df['lag_navn'] = df['team'].map(lag)
        df['lag_short'] = df['team'].map(lag_short)
        df['posisjon'] = df['element_type'].map(posisjoner)
        
        self.players_df = df
        return df
    
    def beregn_metrics(self):
        """Beregner nyttige FPL metrics"""
        if self.players_df is None:
            print("Ingen spillerdata. Kjør lag_spillerdataframe() først.")
            return None
        
        df = self.players_df.copy()
        
        # Konverter pris fra 10ths til millioner
        df['pris_mill'] = df['now_cost'] / 10
        
        # Poeng per million (PPM)
        df['ppm'] = df['total_points'] / df['pris_mill']
        
        # Form (gjennomsnitt siste kamper)
        df['form_num'] = pd.to_numeric(df['form'], errors='coerce')
        
        # Poeng per kamp (per 90 minutter)
        df['ppk'] = df.apply(
            lambda x: x['total_points'] / x['minutes'] * 90 if x['minutes'] > 0 else 0,
            axis=1
        )
        
        # Valgt av prosent
        df['valgt_prosent'] = pd.to_numeric(df['selected_by_percent'], errors='coerce')
        
        # ICT Index komponenter
        df['ict_index_num'] = pd.to_numeric(df['ict_index'], errors='coerce')
        df['influence'] = pd.to_numeric(df['influence'], errors='coerce')
        df['creativity'] = pd.to_numeric(df['creativity'], errors='coerce')
        df['threat'] = pd.to_numeric(df['threat'], errors='coerce')
        
        # Expected stats
        df['expected_goals'] = pd.to_numeric(df['expected_goals'], errors='coerce')
        df['expected_assists'] = pd.to_numeric(df['expected_assists'], errors='coerce')
        df['expected_goal_involvements'] = pd.to_numeric(df['expected_goal_involvements'], errors='coerce')
        
        # Bonus og andre stats
        df['bonus'] = pd.to_numeric(df['bonus'], errors='coerce')
        df['bps'] = pd.to_numeric(df['bps'], errors='coerce')
        
        # Rolling form (siste 3 gameweeks)
        df['points_per_game'] = pd.to_numeric(df['points_per_game'], errors='coerce')
        
        # Clean sheets og defensiv statistikk
        df['clean_sheets'] = pd.to_numeric(df['clean_sheets'], errors='coerce')
        df['goals_conceded'] = pd.to_numeric(df['goals_conceded'], errors='coerce')
        
        return df
    
    def beregn_fixture_difficulty(self, team_id, antall_kamper=5):
        """
        Beregner fixture difficulty for et lag de neste X kampene.
        Lavere score = lettere kamper
        """
        if self.fixtures is None:
            return None
        
        # Filtrer kommende kamper for laget
        kommende = self.fixtures[
            ((self.fixtures['team_h'] == team_id) | (self.fixtures['team_a'] == team_id)) &
            (self.fixtures['finished'] == False)
        ].head(antall_kamper)
        
        if len(kommende) == 0:
            return None
        
        total_difficulty = 0
        for _, kamp in kommende.iterrows():
            if kamp['team_h'] == team_id:
                # Hjemmekamp - bruk team_h_difficulty (vanskelighetsgrad for hjemmelaget)
                difficulty = kamp['team_h_difficulty']
            else:
                # Bortekamp - bruk team_a_difficulty (vanskelighetsgrad for bortelaget)
                difficulty = kamp['team_a_difficulty']
            total_difficulty += difficulty
        
        avg_difficulty = total_difficulty / len(kommende)
        return avg_difficulty
    
    def beregn_team_attack_strength(self):
        """Beregner angrepssstyrke for hvert lag basert på xG og mål"""
        if self.teams_df is None or self.players_df is None:
            return {}
        
        team_strength = {}
        for _, team in self.teams_df.iterrows():
            team_id = team['id']
            team_players = self.players_df[self.players_df['team'] == team_id]
            
            # Konverter expected_goals til numerisk og sum
            team_players_xg = pd.to_numeric(team_players['expected_goals'], errors='coerce').fillna(0)
            total_xg = float(team_players_xg.sum())
            
            # Offensive styrke-rating fra API
            strength_attack = float((team['strength_attack_home'] + team['strength_attack_away']) / 2)
            
            team_strength[team_id] = {
                'xg_total': total_xg,
                'strength_attack': strength_attack,
                'combined_attack': float(total_xg * 0.6 + strength_attack * 0.4)  # Vektet kombinasjon
            }
        
        return team_strength
    
    def beregn_team_defense_strength(self):
        """Beregner forsvarsstyrke for hvert lag"""
        if self.teams_df is None or self.players_df is None:
            return {}
        
        team_defense = {}
        for _, team in self.teams_df.iterrows():
            team_id = team['id']
            team_players = self.players_df[self.players_df['team'] == team_id]
            
            # Clean sheets fra forsvarsspillere
            defenders = team_players[team_players['element_type'] <= 2]  # GK og DEF
            total_clean_sheets = pd.to_numeric(defenders['clean_sheets'], errors='coerce').fillna(0).sum()
            
            # Defensive styrke-rating fra API
            strength_defense = float((team['strength_defence_home'] + team['strength_defence_away']) / 2)
            
            team_defense[team_id] = {
                'clean_sheets': float(total_clean_sheets),
                'strength_defense': strength_defense,
                'combined_defense': float(total_clean_sheets * 0.5 + (6 - strength_defense) * 10)  # Høyere = bedre
            }
        
        return team_defense
    
    def beregn_avansert_spiss_score(self, vekter=None):
        """
        Beregner forventede poeng per kamp (xPts) for spisser.
        
        Formel: xPts = 4×xG + 3×xA + MinPts + Bonus
        
        FPL-poeng for spisser:
        - Mål: 4 poeng
        - Assist: 3 poeng
        - Spilletid: 1-2 poeng
        - Bonus: 0-3 poeng
        """
        df = self.beregn_metrics()
        if df is None:
            return None
        
        # Filtrer kun spisser
        df = df[df['posisjon'] == 'FWD'].copy()
        
        # Filtrer først på spillere med nok minutter for å redusere API-kall
        relevant_df = df[df['minutes'] >= 90].copy()
        
        # Hent spilletidsdata for siste 4 kamper (parallelt, kun relevante spillere)
        player_ids = relevant_df['id'].tolist()
        stats_dict = self.hent_siste_4_kamper_batch(player_ids)
        
        # Sett default verdier
        df['starts_siste_4'] = 0
        df['minutter_siste_4'] = 0
        df['avg_minutes_siste_4'] = 0.0
        df['ppg_siste_4'] = 0.0
        
        # Oppdater med hentet data
        for idx, row in df.iterrows():
            player_id = row['id']
            if player_id in stats_dict:
                stats = stats_dict[player_id]
                df.at[idx, 'starts_siste_4'] = stats['starts_siste_4']
                df.at[idx, 'minutter_siste_4'] = stats['minutter_siste_4']
                df.at[idx, 'avg_minutes_siste_4'] = stats['minutter_siste_4'] / 4
                df.at[idx, 'ppg_siste_4'] = stats.get('ppg_siste_4', 0)
        
        # Beregn spilletid-sannsynlighet basert på siste 4 kamper
        df['start_rate'] = df['starts_siste_4'] / 4
        df['minutes_rate'] = (df['minutter_siste_4'] / 4) / 90
        df['playing_time_probability'] = df['start_rate'] * 0.80 + df['minutes_rate'] * 0.20
        
        # Beregn kamper spilt for laget
        df['kamper_spilt'] = df.apply(
            lambda x: self._get_team_games_played(x['team']),
            axis=1
        )
        df['kamper_spilt'] = df['kamper_spilt'].clip(lower=1)
        
        # Beregn sesong PPG for form-sammenligning
        df['ppg_sesong'] = df['total_points'] / df['kamper_spilt']
        
        # 1. xG per kamp
        df['xG_per_match'] = df['expected_goals'] / df['kamper_spilt']
        
        # 2. xA per kamp
        df['xA_per_match'] = df['expected_assists'] / df['kamper_spilt']
        
        # 3. MinPts: Appearance points basert på siste 4 kamper
        df['MinPts'] = df['avg_minutes_siste_4'].apply(
            lambda x: 2.0 if x >= 60 else (1.0 if x > 0 else 0.0)
        )
        
        # 4. Bonus: 0.04 * BPS90
        df['bps_per_90'] = df.apply(
            lambda x: (x['bps'] / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        df['Bonus_per_match'] = 0.04 * df['bps_per_90']
        
        # BEREGN FORVENTEDE POENG PER KAMP (BASE)
        # Spiss: 4 poeng per mål, 3 poeng per assist
        df['xPts_base'] = (
            4 * df['xG_per_match'] +
            3 * df['xA_per_match'] +
            df['MinPts'] +
            df['Bonus_per_match']
        )
        
        # JUSTER FOR SPILLETID-SANNSYNLIGHET
        df['xPts_per_match'] = df['xPts_base'] * df['playing_time_probability']
        
        # JUSTER FOR FORM (siste 4 kamper vs sesong)
        # Spillere i god form får boost (maks +20%), spillere i dårlig form får reduksjon (maks -20%)
        df['form_ratio'] = df.apply(
            lambda x: x['ppg_siste_4'] / x['ppg_sesong'] if x['ppg_sesong'] > 0 else 1.0,
            axis=1
        )
        # Begrens form_multiplier til 0.8 - 1.2 (±20%)
        df['form_multiplier'] = (0.8 + df['form_ratio'] * 0.2).clip(0.8, 1.2)
        df['xPts_with_form'] = df['xPts_per_match'] * df['form_multiplier']
        
        # Juster for fixture difficulty (neste 5 kamper)
        if self.fixtures is not None:
            team_fixture_difficulty = {}
            for team_id in df['team'].unique():
                fdr = self.beregn_fixture_difficulty(team_id, 5)
                team_fixture_difficulty[team_id] = fdr if fdr else 3
            
            df['fixture_difficulty'] = df['team'].map(team_fixture_difficulty)
            df['fixture_multiplier'] = 1.2 - (df['fixture_difficulty'] - 2) * 0.1
            df['xPts_adjusted'] = df['xPts_with_form'] * df['fixture_multiplier']
        else:
            df['fixture_difficulty'] = 3
            df['xPts_adjusted'] = df['xPts_with_form']
        
        # Behold gammel kolonne for kompatibilitet
        df['total_vektet_spiss_vurdering'] = df['xPts_adjusted']
        
        # Ekstra kolonner for visning
        df['xg_per_90'] = df.apply(
            lambda x: (x['expected_goals'] / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        df['bonus_per_kamp'] = df['Bonus_per_match']
        
        # Team attack strength (for visning)
        team_attack = self.beregn_team_attack_strength()
        df['team_attack_strength'] = df['team'].apply(
            lambda x: team_attack.get(x, {}).get('combined_attack', 50)
        )
        
        return df
    
    def beste_spisser_avansert(self, antall=15, min_minutter=180, maks_pris=None):
        """Finner de beste spissene basert på forventede poeng per kamp (xPts)"""
        df = self.beregn_avansert_spiss_score()
        
        if df is None:
            return None
        
        # Filtrer på minimum spilletid
        df = df[df['minutes'] >= min_minutter]
        
        # Filtrer på pris hvis spesifisert
        if maks_pris:
            df = df[df['pris_mill'] <= maks_pris]
        
        # Velg relevante kolonner
        kolonner = [
            'web_name', 'lag_short', 'pris_mill', 'xPts_adjusted', 'form_multiplier',
            'playing_time_probability', 'xG_per_match', 'xA_per_match',
            'fixture_difficulty', 'ppm', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter etter xPts_adjusted
        resultat = df[kolonner].sort_values(by='xPts_adjusted', ascending=False).head(antall)
        
        # Rund av for bedre lesbarhet
        resultat['xPts_adjusted'] = resultat['xPts_adjusted'].round(2)
        resultat['form_multiplier'] = resultat['form_multiplier'].round(2)
        resultat['playing_time_probability'] = (resultat['playing_time_probability'] * 100).round(0)
        resultat['xG_per_match'] = resultat['xG_per_match'].round(2)
        resultat['xA_per_match'] = resultat['xA_per_match'].round(2)
        resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
        resultat['ppm'] = resultat['ppm'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        # Gi kolonnene kortere, mer lesbare navn
        resultat = resultat.rename(columns={
            'web_name': 'name',
            'lag_short': 'lag',
            'pris_mill': 'pris',
            'xPts_adjusted': 'xPts',
            'form_multiplier': 'form',
            'playing_time_probability': 'play_%',
            'xG_per_match': 'xG',
            'xA_per_match': 'xA',
            'fixture_difficulty': 'fix_diff'
        })
        
        return resultat
    
    def finn_differentials(self, posisjon='FWD', maks_eierskap=15.0, min_score=60, antall=10):
        """
        Finner "differential" spillere - gode spillere med lavt eierskap
        Perfekt for å skille seg ut i mini-leagues
        """
        if posisjon == 'FWD':
            df = self.beregn_avansert_spiss_score()
            score_kolonne = 'total_vektet_spiss_vurdering'
        elif posisjon == 'MID':
            df = self.beregn_avansert_midtbane_score()
            score_kolonne = 'total_vektet_midtbane_vurdering'
        elif posisjon == 'DEF':
            df = self.beregn_avansert_forsvar_score()
            score_kolonne = 'total_vektet_forsvar_vurdering'
        else:
            df = self.beregn_metrics()
            df = df[df['posisjon'] == posisjon]
            score_kolonne = 'ppm'
        
        if df is None:
            return None
        
        # Filtrer på eierskap og score
        df_filtered = df[
            (df['valgt_prosent'] <= maks_eierskap) &
            (df['minutes'] >= 180)
        ]
        
        if score_kolonne in df_filtered.columns:
            df_filtered = df_filtered[df_filtered[score_kolonne] >= min_score]
            sortere_etter = score_kolonne
        else:
            sortere_etter = 'ppm'
        
        kolonner = [
            'web_name', 'lag_short', 'posisjon', 'pris_mill',
            'total_points', 'form_num', 'valgt_prosent', 'ppm'
        ]
        
        if score_kolonne in df_filtered.columns:
            kolonner.insert(4, score_kolonne)
        
        resultat = df_filtered[kolonner].sort_values(by=sortere_etter, ascending=False).head(antall)
        
        return resultat
    
    def sammenlign_spillere(self, spiller_navn_liste):
        """Sammenligner spesifikke spillere side-by-side"""
        df = self.beregn_metrics()
        
        if df is None:
            return None
        
        # Finn spillere basert på navn (case-insensitive partial match)
        spillere = []
        for navn in spiller_navn_liste:
            match = df[df['web_name'].str.contains(navn, case=False, na=False)]
            if not match.empty:
                spillere.append(match.iloc[0])
        
        if not spillere:
            print("Ingen spillere funnet")
            return None
        
        sammenligning = pd.DataFrame(spillere)
        
        kolonner = [
            'web_name', 'lag_navn', 'posisjon', 'pris_mill',
            'total_points', 'form_num', 'ppm', 'expected_goals',
            'expected_assists', 'bonus', 'ict_index_num', 'valgt_prosent'
        ]
        
        return sammenligning[kolonner]
    
    def beregn_avansert_midtbane_score(self, vekter=None):
        """
        Beregner forventede poeng per kamp (xPts) for midtbanespillere.
        
        Formel: xPts = 5×xG + 3×xA + MinPts + Bonus + CS_bonus
        
        FPL-poeng for midtbanespillere:
        - Mål: 5 poeng
        - Assist: 3 poeng
        - Clean sheet: 1 poeng
        - Spilletid: 1-2 poeng
        - Bonus: 0-3 poeng
        """
        df = self.beregn_metrics()
        if df is None:
            return None
        
        # Filtrer kun midtbanespillere
        df = df[df['posisjon'] == 'MID'].copy()
        
        # Filtrer først på spillere med nok minutter for å redusere API-kall
        relevant_df = df[df['minutes'] >= 90].copy()
        
        # Hent spilletidsdata for siste 4 kamper (parallelt, kun relevante spillere)
        player_ids = relevant_df['id'].tolist()
        stats_dict = self.hent_siste_4_kamper_batch(player_ids)
        
        # Sett default verdier
        df['starts_siste_4'] = 0
        df['minutter_siste_4'] = 0
        df['avg_minutes_siste_4'] = 0.0
        df['ppg_siste_4'] = 0.0
        
        # Oppdater med hentet data
        for idx, row in df.iterrows():
            player_id = row['id']
            if player_id in stats_dict:
                stats = stats_dict[player_id]
                df.at[idx, 'starts_siste_4'] = stats['starts_siste_4']
                df.at[idx, 'minutter_siste_4'] = stats['minutter_siste_4']
                df.at[idx, 'avg_minutes_siste_4'] = stats['minutter_siste_4'] / 4
                df.at[idx, 'ppg_siste_4'] = stats.get('ppg_siste_4', 0)
        
        # Beregn spilletid-sannsynlighet basert på siste 4 kamper
        df['start_rate'] = df['starts_siste_4'] / 4
        df['minutes_rate'] = (df['minutter_siste_4'] / 4) / 90
        df['playing_time_probability'] = df['start_rate'] * 0.80 + df['minutes_rate'] * 0.20
        
        # Beregn kamper spilt for laget
        df['kamper_spilt'] = df.apply(
            lambda x: self._get_team_games_played(x['team']),
            axis=1
        )
        df['kamper_spilt'] = df['kamper_spilt'].clip(lower=1)
        
        # Beregn sesong PPG for form-sammenligning
        df['ppg_sesong'] = df['total_points'] / df['kamper_spilt']
        
        # 1. xG per kamp
        df['xG_per_match'] = df['expected_goals'] / df['kamper_spilt']
        
        # 2. xA per kamp
        df['xA_per_match'] = df['expected_assists'] / df['kamper_spilt']
        
        # 3. CS probability (midtbanespillere får 1 poeng for CS)
        team_xga = {}
        for team_id in df['team'].unique():
            team_players = self.players_df[self.players_df['team'] == team_id]
            defenders = team_players[team_players['element_type'] == 2]
            
            if not defenders.empty:
                top_defender = defenders.sort_values(by='minutes', ascending=False).iloc[0]
                gc = pd.to_numeric(top_defender['goals_conceded'], errors='coerce')
                minutes = pd.to_numeric(top_defender['minutes'], errors='coerce')
                
                if pd.notna(gc) and pd.notna(minutes) and minutes > 0:
                    games_played = minutes / 90
                    xga_per_game = gc / games_played if games_played > 0 else 1.5
                else:
                    xga_per_game = 1.5
            else:
                xga_per_game = 1.5
            
            team_xga[team_id] = xga_per_game
        
        df['team_xga'] = df['team'].map(team_xga)
        df['CS_prob'] = np.exp(-df['team_xga'])
        
        # 4. MinPts: Appearance points basert på siste 4 kamper
        df['MinPts'] = df['avg_minutes_siste_4'].apply(
            lambda x: 2.0 if x >= 60 else (1.0 if x > 0 else 0.0)
        )
        
        # 5. Bonus: 0.04 * BPS90
        df['bps_per_90'] = df.apply(
            lambda x: (x['bps'] / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        df['Bonus_per_match'] = 0.04 * df['bps_per_90']
        
        # BEREGN FORVENTEDE POENG PER KAMP (BASE)
        # Midtbane: 5 poeng per mål, 3 poeng per assist, 1 poeng for CS
        df['xPts_base'] = (
            5 * df['xG_per_match'] +
            3 * df['xA_per_match'] +
            1 * df['CS_prob'] +
            df['MinPts'] +
            df['Bonus_per_match']
        )
        
        # JUSTER FOR SPILLETID-SANNSYNLIGHET
        df['xPts_per_match'] = df['xPts_base'] * df['playing_time_probability']
        
        # JUSTER FOR FORM (siste 4 kamper vs sesong)
        # Spillere i god form får boost (maks +20%), spillere i dårlig form får reduksjon (maks -20%)
        df['form_ratio'] = df.apply(
            lambda x: x['ppg_siste_4'] / x['ppg_sesong'] if x['ppg_sesong'] > 0 else 1.0,
            axis=1
        )
        # Begrens form_multiplier til 0.8 - 1.2 (±20%)
        df['form_multiplier'] = (0.8 + df['form_ratio'] * 0.2).clip(0.8, 1.2)
        df['xPts_with_form'] = df['xPts_per_match'] * df['form_multiplier']
        
        # Juster for fixture difficulty (neste 5 kamper)
        if self.fixtures is not None:
            team_fixture_difficulty = {}
            for team_id in df['team'].unique():
                fdr = self.beregn_fixture_difficulty(team_id, 5)
                team_fixture_difficulty[team_id] = fdr if fdr else 3
            
            df['fixture_difficulty'] = df['team'].map(team_fixture_difficulty)
            df['fixture_multiplier'] = 1.2 - (df['fixture_difficulty'] - 2) * 0.1
            df['xPts_adjusted'] = df['xPts_with_form'] * df['fixture_multiplier']
        else:
            df['fixture_difficulty'] = 3
            df['xPts_adjusted'] = df['xPts_with_form']
        
        # Behold gammel kolonne for kompatibilitet
        df['total_vektet_midtbane_vurdering'] = df['xPts_adjusted']
        
        # Ekstra kolonner for visning
        df['xgi_per_90'] = df.apply(
            lambda x: ((x['expected_goals'] + x['expected_assists']) / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        df['xgi'] = df['expected_goals'] + df['expected_assists']
        df['bonus_per_kamp'] = df['Bonus_per_match']
        df['creativity_num'] = pd.to_numeric(df['creativity'], errors='coerce').fillna(0)
        
        return df
    
    def beste_midtbanespillere(self, antall=15, min_minutter=180, maks_pris=None):
        """Finner de beste midtbanespillerne basert på forventede poeng per kamp (xPts)"""
        df = self.beregn_avansert_midtbane_score()
        
        if df is None:
            return None
        
        # Filtrer på minimum spilletid
        df = df[df['minutes'] >= min_minutter]
        
        # Filtrer på pris hvis spesifisert
        if maks_pris:
            df = df[df['pris_mill'] <= maks_pris]
        
        # Velg relevante kolonner
        kolonner = [
            'web_name', 'lag_short', 'pris_mill', 'xPts_adjusted', 'form_multiplier',
            'playing_time_probability', 'xG_per_match', 'xA_per_match',
            'fixture_difficulty', 'ppm', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter etter xPts_adjusted
        resultat = df[kolonner].sort_values(by='xPts_adjusted', ascending=False).head(antall)
        
        # Rund av for bedre lesbarhet
        resultat['xPts_adjusted'] = resultat['xPts_adjusted'].round(2)
        resultat['form_multiplier'] = resultat['form_multiplier'].round(2)
        resultat['playing_time_probability'] = (resultat['playing_time_probability'] * 100).round(0)
        resultat['xG_per_match'] = resultat['xG_per_match'].round(2)
        resultat['xA_per_match'] = resultat['xA_per_match'].round(2)
        resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
        resultat['ppm'] = resultat['ppm'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        # Gi kolonnene kortere, mer lesbare navn
        resultat = resultat.rename(columns={
            'web_name': 'name',
            'lag_short': 'lag',
            'pris_mill': 'pris',
            'xPts_adjusted': 'xPts',
            'form_multiplier': 'form',
            'playing_time_probability': 'play_%',
            'xG_per_match': 'xG',
            'xA_per_match': 'xA',
            'fixture_difficulty': 'fix_diff'
        })
        
        return resultat
    
    def beregn_avansert_keeper_score(self):
        """
        Beregner forventede poeng per kamp (xPts) for keepere.
        
        Formel: xPts = (4×CS_prob + 0.33×Saves_per_match + MinPts + Bonus) × play_prob × fixture_mult
        
        FPL-poeng for keepere:
        - Clean sheet: 4 poeng
        - Hver 3. redning: 1 poeng (≈ 0.33 per redning)
        - Spilletid 60+ min: 2 poeng
        - Spilletid 1-59 min: 1 poeng
        - Bonus: 0-3 poeng
        """
        df = self.beregn_metrics()
        if df is None:
            return None
        
        # Filtrer kun keepere (element_type = 1)
        df = df[df['posisjon'] == 'GKP'].copy()
        
        # Filtrer først på spillere med nok minutter for å redusere API-kall
        relevant_df = df[df['minutes'] >= 90].copy()
        
        # Hent spilletidsdata for siste 4 kamper (parallelt, kun relevante spillere)
        player_ids = relevant_df['id'].tolist()
        stats_dict = self.hent_siste_4_kamper_batch(player_ids)
        
        # Sett default verdier
        df['starts_siste_4'] = 0
        df['minutter_siste_4'] = 0
        df['avg_minutes_siste_4'] = 0.0
        df['ppg_siste_4'] = 0.0
        
        # Oppdater med hentet data
        for idx, row in df.iterrows():
            player_id = row['id']
            if player_id in stats_dict:
                stats = stats_dict[player_id]
                df.at[idx, 'starts_siste_4'] = stats['starts_siste_4']
                df.at[idx, 'minutter_siste_4'] = stats['minutter_siste_4']
                df.at[idx, 'avg_minutes_siste_4'] = stats['minutter_siste_4'] / 4
                df.at[idx, 'ppg_siste_4'] = stats.get('ppg_siste_4', 0)
        
        # Beregn spilletid-sannsynlighet basert på siste 4 kamper
        df['start_rate'] = df['starts_siste_4'] / 4
        df['minutes_rate'] = (df['minutter_siste_4'] / 4) / 90
        df['playing_time_probability'] = df['start_rate'] * 0.80 + df['minutes_rate'] * 0.20
        
        # Beregn kamper spilt for laget
        df['kamper_spilt'] = df.apply(
            lambda x: self._get_team_games_played(x['team']),
            axis=1
        )
        df['kamper_spilt'] = df['kamper_spilt'].clip(lower=1)
        
        # Beregn sesong PPG for form-sammenligning
        df['ppg_sesong'] = df['total_points'] / df['kamper_spilt']
        
        # 1. CS probability basert på lagets xGA
        team_xga = {}
        for team_id in df['team'].unique():
            team_players = self.players_df[self.players_df['team'] == team_id]
            # Bruk keeperen eller forsvarsspilleren med mest spilletid
            defenders = team_players[team_players['element_type'] <= 2]  # GK og DEF
            
            if len(defenders) > 0:
                defenders_sorted = defenders.sort_values(by='minutes', ascending=False)
                top_defender = defenders_sorted.iloc[0]
                
                gc = pd.to_numeric(top_defender['goals_conceded'], errors='coerce')
                minutes = pd.to_numeric(top_defender['minutes'], errors='coerce')
                
                if pd.notna(gc) and pd.notna(minutes) and minutes > 0:
                    games_played = minutes / 90
                    xga_per_game = gc / games_played if games_played > 0 else 1.5
                else:
                    xga_per_game = 1.5
            else:
                xga_per_game = 1.5
            
            team_xga[team_id] = xga_per_game
        
        df['team_xga'] = df['team'].map(team_xga)
        df['CS_prob'] = np.exp(-df['team_xga'])
        
        # 2. Saves per kamp
        df['saves'] = pd.to_numeric(df['saves'], errors='coerce').fillna(0)
        df['saves_per_match'] = df['saves'] / df['kamper_spilt']
        
        # 3. MinPts: Appearance points basert på siste 4 kamper
        df['MinPts'] = df['avg_minutes_siste_4'].apply(
            lambda x: 2.0 if x >= 60 else (1.0 if x > 0 else 0.0)
        )
        
        # 4. Bonus: 0.04 * BPS90
        df['bps_per_90'] = df.apply(
            lambda x: (x['bps'] / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        df['Bonus_per_match'] = 0.04 * df['bps_per_90']
        
        # BEREGN FORVENTEDE POENG PER KAMP (BASE)
        # Keeper: 4 poeng for CS, 0.33 per save
        df['xPts_base'] = (
            4 * df['CS_prob'] +
            0.33 * df['saves_per_match'] +
            df['MinPts'] +
            df['Bonus_per_match']
        )
        
        # JUSTER FOR SPILLETID-SANNSYNLIGHET
        df['xPts_per_match'] = df['xPts_base'] * df['playing_time_probability']
        
        # JUSTER FOR FORM (siste 4 kamper vs sesong)
        df['form_ratio'] = df.apply(
            lambda x: x['ppg_siste_4'] / x['ppg_sesong'] if x['ppg_sesong'] > 0 else 1.0,
            axis=1
        )
        df['form_multiplier'] = (0.8 + df['form_ratio'] * 0.2).clip(0.8, 1.2)
        df['xPts_with_form'] = df['xPts_per_match'] * df['form_multiplier']
        
        # Juster for fixture difficulty (neste 5 kamper)
        if self.fixtures is not None:
            team_fixture_difficulty = {}
            for team_id in df['team'].unique():
                fdr = self.beregn_fixture_difficulty(team_id, 5)
                team_fixture_difficulty[team_id] = fdr if fdr else 3
            
            df['fixture_difficulty'] = df['team'].map(team_fixture_difficulty)
            # For keepere: lavere FDR = høyere CS-sjanse = høyere xPts
            df['fixture_multiplier'] = 1.2 - (df['fixture_difficulty'] - 2) * 0.1
            df['xPts_adjusted'] = df['xPts_with_form'] * df['fixture_multiplier']
        else:
            df['fixture_difficulty'] = 3
            df['xPts_adjusted'] = df['xPts_with_form']
        
        return df
    
    def beste_keepere(self, antall=15, min_minutter=180, maks_pris=None):
        """Finner de beste keeperne basert på forventede poeng per kamp (xPts)"""
        df = self.beregn_avansert_keeper_score()
        
        if df is None:
            return None
        
        # Filtrer på minimum spilletid
        df = df[df['minutes'] >= min_minutter]
        
        # Filtrer på pris hvis spesifisert
        if maks_pris:
            df = df[df['pris_mill'] <= maks_pris]
        
        # Velg relevante kolonner
        kolonner = [
            'web_name', 'lag_short', 'pris_mill', 'xPts_adjusted', 'form_multiplier',
            'playing_time_probability', 'CS_prob', 'saves_per_match',
            'fixture_difficulty', 'ppm', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter etter xPts_adjusted
        resultat = df[kolonner].sort_values(by='xPts_adjusted', ascending=False).head(antall)
        
        # Rund av for bedre lesbarhet
        resultat['xPts_adjusted'] = resultat['xPts_adjusted'].round(2)
        resultat['form_multiplier'] = resultat['form_multiplier'].round(2)
        resultat['playing_time_probability'] = (resultat['playing_time_probability'] * 100).round(0)
        resultat['CS_prob'] = (resultat['CS_prob'] * 100).round(1)
        resultat['saves_per_match'] = resultat['saves_per_match'].round(1)
        resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
        resultat['ppm'] = resultat['ppm'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        # Gi kolonnene kortere, mer lesbare navn
        resultat = resultat.rename(columns={
            'web_name': 'name',
            'lag_short': 'lag',
            'pris_mill': 'pris',
            'xPts_adjusted': 'xPts',
            'form_multiplier': 'form',
            'playing_time_probability': 'play_%',
            'CS_prob': 'CS_%',
            'saves_per_match': 'saves',
            'fixture_difficulty': 'fix_diff'
        })
        
        return resultat
    
    def beregn_avansert_forsvar_score(self, vekter=None):
        """
        Enkel, forklarbar forsvarsspiller-modell basert på forventede poeng per kamp:
        xPts = 4*CS + 6*xG + 3*xA + MinPts + Bonus
        
        hvor:
        - CS = exp(-xGA_team) - clean sheet probability basert på lagets xGA
        - xG = xG90_player * minutes / 90 - forventede mål
        - xA = xA90_player * minutes / 90 - forventede assist
        - MinPts = 2P(minutes≥60) + 1P(0<minutes<60) - poeng for spilletid
        - Bonus ≈ 0.04 * BPS90 * minutes/90 - forventede bonuspoeng
        """
        df = self.beregn_metrics()
        if df is None:
            return None
        
        # Filtrer kun forsvarsspillere
        df = df[df['posisjon'] == 'DEF'].copy()
        
        # Beregn faktisk antall kamper laget har spilt
        # Vi må estimere dette fra lagets data siden 'appearances' ikke finnes i API
        team_games_played = {}
        
        if self.fixtures is not None:
            # Beregn antall kamper hvert lag har spilt (finished games)
            for team_id in df['team'].unique():
                finished_games = self.fixtures[
                    ((self.fixtures['team_h'] == team_id) | (self.fixtures['team_a'] == team_id)) &
                    (self.fixtures['finished'] == True)
                ]
                team_games_played[team_id] = len(finished_games) if len(finished_games) > 0 else 20
        else:
            # Hvis vi ikke har fixtures, anta ~20 kamper spilt
            for team_id in df['team'].unique():
                team_games_played[team_id] = 20
        
        # Legg til lagets antall spilte kamper
        df['team_games_played'] = df['team'].map(team_games_played)
        df['team_games_played'] = df['team_games_played'].fillna(20)  # Default hvis noe går galt
        
        # BEREGN SPILLETID-SANNSYNLIGHET BASERT PÅ SISTE 4 KAMPER
        # Filtrer først på spillere med nok minutter for å redusere API-kall
        relevant_df = df[df['minutes'] >= 90].copy()
        
        # Hent spilletidsdata for siste 4 kamper (parallelt, kun relevante spillere)
        player_ids = relevant_df['id'].tolist()
        siste_4_stats = self.hent_siste_4_kamper_batch(player_ids)
        
        # Legg til siste 4 kamper data
        df['starts_siste_4'] = df['id'].apply(lambda x: siste_4_stats.get(x, {}).get('starts_siste_4', 0))
        df['minutter_siste_4'] = df['id'].apply(lambda x: siste_4_stats.get(x, {}).get('minutter_siste_4', 0))
        df['kamper_siste_4'] = df['id'].apply(lambda x: siste_4_stats.get(x, {}).get('antall_kamper', 4))
        df['ppg_siste_4'] = df['id'].apply(lambda x: siste_4_stats.get(x, {}).get('ppg_siste_4', 0))
        df['kamper_siste_4'] = df['kamper_siste_4'].replace(0, 4)  # Unngå divisjon med 0
        
        # Start rate basert på siste 4 kamper
        df['start_rate'] = (df['starts_siste_4'] / df['kamper_siste_4']).clip(0, 1)
        
        # Minutes rate basert på siste 4 kamper
        df['avg_minutes_siste_4'] = df['minutter_siste_4'] / df['kamper_siste_4']
        df['minutes_rate'] = (df['avg_minutes_siste_4'] / 90).clip(0, 1)
        
        # Kombinert spilletid-sannsynlighet (viktede kombinasjon)
        # 80% vekt på start_rate (viktigst - starter de?), 20% på minutes_rate
        df['playing_time_probability'] = (
            df['start_rate'] * 0.80 + 
            df['minutes_rate'] * 0.20
        )
        
        # For xPts beregninger, bruk fortsatt totale stats (mer pålitelig for xG/xA)
        df['kamper_spilt'] = df['team_games_played']
        df['avg_minutes_per_game'] = df['minutes'] / df['team_games_played']
        
        # Beregn sesong PPG for form-sammenligning
        df['ppg_sesong'] = df['total_points'] / df['kamper_spilt']
        
        # 1. Clean Sheet probability: CS = exp(-xGA_team)
        # Beregn lagets xGA (expected goals against) per kamp
        team_xga = {}
        for team_id in df['team'].unique():
            team_players = self.players_df[self.players_df['team'] == team_id]
            # Bruk keeperen eller forsvarsspilleren med mest spilletid for å få lagets goals_conceded
            defenders = team_players[team_players['element_type'] <= 2]  # GK og DEF
            
            if len(defenders) > 0:
                # Finn spilleren med mest minutter (mest representativ for lagets kamper)
                defenders_sorted = defenders.sort_values(by='minutes', ascending=False)
                top_defender = defenders_sorted.iloc[0]
                
                # Bruk denne spillerens goals_conceded og minutter
                gc = pd.to_numeric(top_defender['goals_conceded'], errors='coerce')
                minutes = pd.to_numeric(top_defender['minutes'], errors='coerce')
                
                if pd.notna(gc) and pd.notna(minutes) and minutes > 0:
                    games_played = minutes / 90
                    xga_per_game = gc / games_played if games_played > 0 else 1.5
                else:
                    xga_per_game = 1.5
            else:
                xga_per_game = 1.5
            
            team_xga[team_id] = xga_per_game
        
        df['team_xga'] = df['team'].map(team_xga)
        df['CS_prob'] = np.exp(-df['team_xga'])
        
        # 2. xG per kamp (normalisert til per kamp, ikke per 90)
        df['xG_per_match'] = df['expected_goals'] / df['kamper_spilt']
        
        # 3. xA per kamp
        df['xA_per_match'] = df['expected_assists'] / df['kamper_spilt']
        
        # 4. MinPts: Appearance points basert på siste 4 kamper
        # Bruker avg_minutes_siste_4 for å fange opp nåværende spilletid-situasjon
        df['MinPts'] = df['avg_minutes_siste_4'].apply(
            lambda x: 2.0 if x >= 60 else (1.0 if x > 0 else 0.0)
        )
        
        # 5. Bonus: 0.04 * BPS90 * (minutter/90)
        df['bps_per_90'] = df.apply(
            lambda x: (x['bps'] / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        df['Bonus_per_match'] = 0.04 * df['bps_per_90']
        
        # BEREGN FORVENTEDE POENG PER KAMP (BASE - UTEN SPILLETID-JUSTERING)
        df['xPts_base'] = (
            4 * df['CS_prob'] +
            6 * df['xG_per_match'] +
            3 * df['xA_per_match'] +
            df['MinPts'] +
            df['Bonus_per_match']
        )
        
        # JUSTER FOR SPILLETID-SANNSYNLIGHET
        # Dette reduserer scoren betydelig for benk-spillere
        # En spiller som starter 50% av kampene får kun 50% av xPts
        df['xPts_per_match'] = df['xPts_base'] * df['playing_time_probability']
        
        # JUSTER FOR FORM (siste 4 kamper vs sesong)
        # Spillere i god form får boost (maks +20%), spillere i dårlig form får reduksjon (maks -20%)
        df['form_ratio'] = df.apply(
            lambda x: x['ppg_siste_4'] / x['ppg_sesong'] if x['ppg_sesong'] > 0 else 1.0,
            axis=1
        )
        # Begrens form_multiplier til 0.8 - 1.2 (±20%)
        df['form_multiplier'] = (0.8 + df['form_ratio'] * 0.2).clip(0.8, 1.2)
        df['xPts_with_form'] = df['xPts_per_match'] * df['form_multiplier']
        
        # Juster for fixture difficulty (neste 5 kamper)
        if self.fixtures is not None:
            team_fixture_difficulty = {}
            for team_id in df['team'].unique():
                fdr = self.beregn_fixture_difficulty(team_id, 5)
                team_fixture_difficulty[team_id] = fdr if fdr else 3
            
            df['fixture_difficulty'] = df['team'].map(team_fixture_difficulty)
            # Juster xPts basert på fixture difficulty (lettere kamper = høyere forventet score)
            # Normaliser fixture difficulty fra 2-5 til en multiplikator 0.9-1.1
            df['fixture_multiplier'] = 1.2 - (df['fixture_difficulty'] - 2) * 0.1
            df['xPts_adjusted'] = df['xPts_with_form'] * df['fixture_multiplier']
        else:
            df['fixture_difficulty'] = 3
            df['xPts_adjusted'] = df['xPts_with_form']
        
        # Bruker xPts_adjusted som hovedscore, kombinert med PPM for verdi
        df['total_vektet_forsvar_vurdering'] = (
            df['xPts_adjusted'] * 10 + df['ppm'] * 2
        )
        
        # Behold også komponentene for visning
        df['clean_sheet_potential'] = df['CS_prob'] * 100
        df['xgi_per_90'] = (df['xG_per_match'] + df['xA_per_match']) * 90 / df['avg_minutes_per_game'].clip(lower=1)
        df['bonus_per_kamp'] = df['Bonus_per_match']
        
        return df
    
    def beste_forsvarsspillere(self, antall=15, min_minutter=180, maks_pris=None):
        """Finner de beste forsvarsspillerne basert på forventede poeng per kamp (xPts)"""
        df = self.beregn_avansert_forsvar_score()
        
        if df is None:
            return None
        
        # Filtrer på minimum spilletid
        df = df[df['minutes'] >= min_minutter]
        
        # Filtrer på pris hvis spesifisert
        if maks_pris:
            df = df[df['pris_mill'] <= maks_pris]
        
        # Velg relevante kolonner
        kolonner = [
            'web_name', 'lag_short', 'pris_mill', 'xPts_adjusted', 'form_multiplier',
            'playing_time_probability', 'CS_prob', 'xG_per_match', 'xA_per_match',
            'fixture_difficulty', 'ppm', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter etter xPts_adjusted (som inkluderer spilletid-justering og fixtures)
        resultat = df[kolonner].sort_values(by='xPts_adjusted', ascending=False).head(antall)
        
        # Rund av for bedre lesbarhet
        resultat['xPts_adjusted'] = resultat['xPts_adjusted'].round(2)
        resultat['form_multiplier'] = resultat['form_multiplier'].round(2)
        resultat['playing_time_probability'] = (resultat['playing_time_probability'] * 100).round(0)  # Vis som prosent
        resultat['CS_prob'] = (resultat['CS_prob'] * 100).round(1)  # Vis som prosent
        resultat['xG_per_match'] = resultat['xG_per_match'].round(3)
        resultat['xA_per_match'] = resultat['xA_per_match'].round(3)
        resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
        resultat['ppm'] = resultat['ppm'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        # Gi kolonnene kortere, mer lesbare navn
        resultat = resultat.rename(columns={
            'web_name': 'name',
            'lag_short': 'lag',
            'pris_mill': 'pris',
            'xPts_adjusted': 'xPts',
            'form_multiplier': 'form',
            'playing_time_probability': 'play_%',
            'fixture_difficulty': 'fix_diff',
            'xG_per_match': 'xG',
            'xA_per_match': 'xA'
        })
        
        return resultat
    
    def vis_spillere(self, spiller_navn_liste, posisjon='DEF'):
        """Viser detaljert statistikk for spesifikke spillere"""
        if posisjon == 'DEF':
            df = self.beregn_avansert_forsvar_score()
            kolonner = [
                'web_name', 'lag_short', 'pris_mill', 'xPts_adjusted', 'playing_time_probability',
                'xPts_base', 'CS_prob', 'xG_per_match', 'xA_per_match', 'Bonus_per_match',
                'fixture_difficulty', 'ppm', 'total_points', 'valgt_prosent'
            ]
        elif posisjon == 'MID':
            df = self.beregn_avansert_midtbane_score()
            kolonner = [
                'web_name', 'lag_short', 'pris_mill', 'total_vektet_midtbane_vurdering',
                'xgi_per_90', 'creativity_num', 'form_num', 'fixture_difficulty',
                'ppm', 'bonus_per_kamp', 'total_points', 'valgt_prosent'
            ]
        elif posisjon == 'FWD':
            df = self.beregn_avansert_spiss_score()
            kolonner = [
                'web_name', 'lag_short', 'pris_mill', 'total_vektet_spiss_vurdering',
                'xg_per_90', 'form_num', 'fixture_difficulty', 'team_attack_strength',
                'ppm', 'bonus_per_kamp', 'total_points', 'valgt_prosent'
            ]
        else:
            return None
        
        if df is None:
            return None
        
        # Finn spillere basert på navn (case-insensitive partial match)
        spillere_df = df[df['web_name'].str.contains('|'.join(spiller_navn_liste), case=False, na=False)]
        
        if spillere_df.empty:
            print(f"Ingen spillere funnet med navn: {spiller_navn_liste}")
            return None
        
        resultat = spillere_df[kolonner].copy()
        
        # Rund av for forsvarsspillere
        if posisjon == 'DEF':
            resultat['xPts_adjusted'] = resultat['xPts_adjusted'].round(2)
            resultat['xPts_base'] = resultat['xPts_base'].round(2)
            resultat['playing_time_probability'] = (resultat['playing_time_probability'] * 100).round(0)
            resultat['CS_prob'] = (resultat['CS_prob'] * 100).round(1)
            resultat['xG_per_match'] = resultat['xG_per_match'].round(3)
            resultat['xA_per_match'] = resultat['xA_per_match'].round(3)
            resultat['Bonus_per_match'] = resultat['Bonus_per_match'].round(2)
            resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
            resultat['ppm'] = resultat['ppm'].round(2)
            resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        return resultat.sort_values(by=kolonner[3], ascending=False)
    
    def vis_detaljert_beregning(self, spiller_navn, posisjon='DEF'):
        """
        Viser detaljert steg-for-steg beregning av en spillers rangering.
        Nyttig for å forstå hvorfor en spiller er rangert høyt eller lavt.
        """
        if posisjon == 'DEF':
            df = self.beregn_avansert_forsvar_score()
        elif posisjon == 'MID':
            df = self.beregn_avansert_midtbane_score()
        elif posisjon == 'FWD':
            df = self.beregn_avansert_spiss_score()
        else:
            print(f"Ukjent posisjon: {posisjon}")
            return None
        
        if df is None:
            print("Kunne ikke hente data")
            return None
        
        # Finn spilleren
        spiller_df = df[df['web_name'].str.contains(spiller_navn, case=False, na=False)]
        
        if spiller_df.empty:
            print(f"Fant ingen spiller med navn '{spiller_navn}'")
            return None
        
        spiller = spiller_df.iloc[0]
        
        print("\n" + "="*80)
        print(f"DETALJERT BEREGNING FOR: {spiller['web_name']} ({spiller['lag_short']})")
        print("="*80)
        
        if posisjon == 'DEF':
            self._vis_forsvar_beregning(spiller, df)
        elif posisjon == 'MID':
            self._vis_midtbane_beregning(spiller, df)
        elif posisjon == 'FWD':
            self._vis_spiss_beregning(spiller, df)
        
        return spiller
    
    def _hent_fixture_detaljer(self, team_id, antall=5):
        """Henter detaljer om kommende kamper for et lag"""
        if self.fixtures is None:
            return None
        
        # Finn kommende kamper (ikke finished)
        team_fixtures = self.fixtures[
            ((self.fixtures['team_h'] == team_id) | (self.fixtures['team_a'] == team_id)) &
            (self.fixtures['finished'] == False)
        ].head(antall)
        
        if team_fixtures.empty:
            return None
        
        fixture_list = []
        for _, fixture in team_fixtures.iterrows():
            is_home = fixture['team_h'] == team_id
            opponent_id = fixture['team_a'] if is_home else fixture['team_h']
            # FDR for laget: Hvis hjemmekamp, bruk team_h_difficulty. Hvis bortekamp, bruk team_a_difficulty.
            fdr = fixture['team_h_difficulty'] if is_home else fixture['team_a_difficulty']
            
            # Finn lagnavnet
            opponent_name = "???"
            if self.teams_df is not None:
                opponent_row = self.teams_df[self.teams_df['id'] == opponent_id]
                if not opponent_row.empty:
                    opponent_name = opponent_row.iloc[0]['short_name']
            
            venue = "H" if is_home else "A"
            gw = fixture.get('event', '?')
            
            fixture_list.append({
                'gw': gw,
                'opponent': opponent_name,
                'venue': venue,
                'fdr': fdr
            })
        
        return fixture_list
    
    def _vis_forsvar_beregning(self, spiller, df):
        """Viser detaljert beregning for forsvarsspiller"""
        
        print(f"\n📊 GRUNNLEGGENDE INFO:")
        print(f"   Pris: £{spiller['pris_mill']:.1f}m")
        print(f"   Total poeng denne sesongen: {spiller['total_points']}")
        print(f"   Valgt av: {spiller['valgt_prosent']:.1f}%")
        
        print(f"\n⏱️ SPILLETID-SANNSYNLIGHET (siste 4 kamper):")
        print(f"   Starter siste 4 kamper: {spiller.get('starts_siste_4', 'N/A')}")
        print(f"   Minutter siste 4 kamper: {spiller.get('minutter_siste_4', 'N/A')}")
        print(f"   Start Rate: {spiller['start_rate']*100:.0f}%")
        print(f"   Minutes Rate: {spiller['minutes_rate']*100:.0f}%")
        print(f"   → playing_time_probability = {spiller['start_rate']:.2f} × 0.80 + {spiller['minutes_rate']:.2f} × 0.20")
        print(f"   → playing_time_probability = {spiller['playing_time_probability']*100:.0f}%")
        
        print(f"\n🛡️ CLEAN SHEET PROBABILITY:")
        print(f"   Lagets xGA per kamp: {spiller['team_xga']:.2f}")
        print(f"   CS_prob = exp(-{spiller['team_xga']:.2f}) = {spiller['CS_prob']*100:.1f}%")
        print(f"   FPL-poeng fra CS: 4 × {spiller['CS_prob']:.3f} = {4*spiller['CS_prob']:.2f}")
        
        print(f"\n⚽ EXPECTED GOALS (xG):")
        print(f"   Total xG denne sesongen: {spiller['expected_goals']:.2f}")
        print(f"   Kamper spilt (lag): {spiller['kamper_spilt']:.0f}")
        print(f"   xG per kamp: {spiller['xG_per_match']:.3f}")
        print(f"   FPL-poeng fra xG: 6 × {spiller['xG_per_match']:.3f} = {6*spiller['xG_per_match']:.2f}")
        
        print(f"\n🎯 EXPECTED ASSISTS (xA):")
        print(f"   Total xA denne sesongen: {spiller['expected_assists']:.2f}")
        print(f"   xA per kamp: {spiller['xA_per_match']:.3f}")
        print(f"   FPL-poeng fra xA: 3 × {spiller['xA_per_match']:.3f} = {3*spiller['xA_per_match']:.2f}")
        
        print(f"\n👟 APPEARANCE POINTS (MinPts) - basert på siste 4 kamper:")
        print(f"   Gj.snitt minutter siste 4 kamper: {spiller['avg_minutes_siste_4']:.0f}")
        print(f"   MinPts: {spiller['MinPts']:.1f} (2 hvis ≥60 min, 1 hvis <60 min, 0 ellers)")
        
        print(f"\n⭐ BONUS POINTS:")
        print(f"   BPS per 90 min: {spiller['bps_per_90']:.1f}")
        print(f"   Bonus per kamp: 0.04 × {spiller['bps_per_90']:.1f} = {spiller['Bonus_per_match']:.2f}")
        
        print(f"\n📈 xPts BEREGNING:")
        print(f"   xPts_base = 4×CS + 6×xG + 3×xA + MinPts + Bonus")
        xpts_cs = 4 * spiller['CS_prob']
        xpts_xg = 6 * spiller['xG_per_match']
        xpts_xa = 3 * spiller['xA_per_match']
        print(f"   xPts_base = {xpts_cs:.2f} + {xpts_xg:.2f} + {xpts_xa:.2f} + {spiller['MinPts']:.2f} + {spiller['Bonus_per_match']:.2f}")
        print(f"   xPts_base = {spiller['xPts_base']:.2f}")
        
        print(f"\n🎮 SPILLETID-JUSTERING:")
        print(f"   xPts_per_match = xPts_base × playing_time_probability")
        print(f"   xPts_per_match = {spiller['xPts_base']:.2f} × {spiller['playing_time_probability']:.2f}")
        print(f"   xPts_per_match = {spiller['xPts_base'] * spiller['playing_time_probability']:.2f}")
        
        print(f"\n📅 FIXTURE-JUSTERING:")
        print(f"   Fixture difficulty (neste 5): {spiller['fixture_difficulty']:.1f}")
        
        # Vis detaljerte fixtures
        fixture_detaljer = self._hent_fixture_detaljer(spiller['team'], 5)
        if fixture_detaljer:
            print(f"   Kommende kamper:")
            for f in fixture_detaljer:
                fdr_bar = "🟢" if f['fdr'] <= 2 else "🟡" if f['fdr'] == 3 else "🟠" if f['fdr'] == 4 else "🔴"
                print(f"      GW{f['gw']}: {f['opponent']} ({f['venue']}) - FDR: {f['fdr']} {fdr_bar}")
            fdr_sum = sum(f['fdr'] for f in fixture_detaljer)
            print(f"   Gjennomsnitt FDR: {fdr_sum}/{len(fixture_detaljer)} = {fdr_sum/len(fixture_detaljer):.1f}")
        
        fixture_mult = 1.2 - (spiller['fixture_difficulty'] - 2) * 0.1
        print(f"   Fixture multiplier: 1.2 - ({spiller['fixture_difficulty']:.1f} - 2) × 0.1 = {fixture_mult:.2f}")
        print(f"   xPts_adjusted = {spiller['xPts_base'] * spiller['playing_time_probability']:.2f} × {fixture_mult:.2f}")
        print(f"   xPts_adjusted = {spiller['xPts_adjusted']:.2f}")
        
        # Finn rangering
        df_sorted = df.sort_values(by='xPts_adjusted', ascending=False).reset_index(drop=True)
        rangering = df_sorted[df_sorted['web_name'] == spiller['web_name']].index[0] + 1
        
        print(f"\n🏆 ENDELIG RESULTAT:")
        print(f"   xPts_adjusted: {spiller['xPts_adjusted']:.2f}")
        print(f"   Rangering blant forsvarsspillere: #{rangering} av {len(df)}")
        print("="*80 + "\n")
    
    def _vis_midtbane_beregning(self, spiller, df):
        """Viser detaljert beregning for midtbanespiller"""
        print(f"\n📊 GRUNNLEGGENDE INFO:")
        print(f"   Pris: £{spiller['pris_mill']:.1f}m")
        print(f"   Total poeng: {spiller['total_points']}")
        print(f"   Form: {spiller['form_num']:.1f}")
        print(f"   Valgt av: {spiller['valgt_prosent']:.1f}%")
        
        print(f"\n⚽ xGI (Expected Goal Involvements):")
        print(f"   xG + xA = {spiller['xgi']:.2f}")
        print(f"   xGI per 90: {spiller['xgi_per_90']:.2f}")
        
        print(f"\n🎨 CREATIVITY:")
        print(f"   Creativity score: {spiller['creativity_num']:.1f}")
        
        print(f"\n📅 FIXTURES:")
        print(f"   Fixture difficulty (neste 5): {spiller['fixture_difficulty']:.1f}")
        
        # Vis detaljerte fixtures
        fixture_detaljer = self._hent_fixture_detaljer(spiller['team'], 5)
        if fixture_detaljer:
            print(f"   Kommende kamper:")
            for f in fixture_detaljer:
                fdr_bar = "🟢" if f['fdr'] <= 2 else "🟡" if f['fdr'] == 3 else "🟠" if f['fdr'] == 4 else "🔴"
                print(f"      GW{f['gw']}: {f['opponent']} ({f['venue']}) - FDR: {f['fdr']} {fdr_bar}")
            fdr_sum = sum(f['fdr'] for f in fixture_detaljer)
            print(f"   Gjennomsnitt FDR: {fdr_sum}/{len(fixture_detaljer)} = {fdr_sum/len(fixture_detaljer):.1f}")
        
        print(f"\n💰 VERDI:")
        print(f"   PPM (poeng per million): {spiller['ppm']:.2f}")
        
        # Finn rangering
        df_sorted = df.sort_values(by='total_vektet_midtbane_vurdering', ascending=False).reset_index(drop=True)
        rangering = df_sorted[df_sorted['web_name'] == spiller['web_name']].index[0] + 1
        
        print(f"\n🏆 ENDELIG RESULTAT:")
        print(f"   Total score: {spiller['total_vektet_midtbane_vurdering']:.1f}")
        print(f"   Rangering blant midtbanespillere: #{rangering} av {len(df)}")
        print("="*80 + "\n")
    
    def _vis_spiss_beregning(self, spiller, df):
        """Viser detaljert beregning for spiss"""
        print(f"\n📊 GRUNNLEGGENDE INFO:")
        print(f"   Pris: £{spiller['pris_mill']:.1f}m")
        print(f"   Total poeng: {spiller['total_points']}")
        print(f"   Form: {spiller['form_num']:.1f}")
        print(f"   Valgt av: {spiller['valgt_prosent']:.1f}%")
        
        print(f"\n⚽ EXPECTED GOALS:")
        print(f"   xG per 90: {spiller['xg_per_90']:.2f}")
        
        print(f"\n💪 LAGETS STYRKE:")
        print(f"   Team attack strength: {spiller['team_attack_strength']:.1f}")
        
        print(f"\n📅 FIXTURES:")
        print(f"   Fixture difficulty (neste 5): {spiller['fixture_difficulty']:.1f}")
        
        # Vis detaljerte fixtures
        fixture_detaljer = self._hent_fixture_detaljer(spiller['team'], 5)
        if fixture_detaljer:
            print(f"   Kommende kamper:")
            for f in fixture_detaljer:
                fdr_bar = "🟢" if f['fdr'] <= 2 else "🟡" if f['fdr'] == 3 else "🟠" if f['fdr'] == 4 else "🔴"
                print(f"      GW{f['gw']}: {f['opponent']} ({f['venue']}) - FDR: {f['fdr']} {fdr_bar}")
            fdr_sum = sum(f['fdr'] for f in fixture_detaljer)
            print(f"   Gjennomsnitt FDR: {fdr_sum}/{len(fixture_detaljer)} = {fdr_sum/len(fixture_detaljer):.1f}")
        
        print(f"\n💰 VERDI:")
        print(f"   PPM (poeng per million): {spiller['ppm']:.2f}")
        
        # Finn rangering
        df_sorted = df.sort_values(by='total_vektet_spiss_vurdering', ascending=False).reset_index(drop=True)
        rangering = df_sorted[df_sorted['web_name'] == spiller['web_name']].index[0] + 1
        
        print(f"\n🏆 ENDELIG RESULTAT:")
        print(f"   Total score: {spiller['total_vektet_spiss_vurdering']:.1f}")
        print(f"   Rangering blant spisser: #{rangering} av {len(df)}")
        print("="*80 + "\n")
    
    def beste_attacking_defenders(self, antall=10, min_minutter=180):
        """Finner de beste offensive forsvarsspillerne (wingbacks med assist-potensial)"""
        df = self.beregn_avansert_forsvar_score()
        
        if df is None:
            return None
        
        # Filtrer på minimum spilletid
        df = df[df['minutes'] >= min_minutter]
        
        # Velg relevante kolonner for attacking defenders
        kolonner = [
            'web_name', 'lag_short', 'pris_mill', 'total_vektet_forsvar_vurdering',
            'xgi_per_90', 'expected_assists', 'clean_sheet_potential',
            'bonus_per_kamp', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter først etter xgi_per_90 for å få de mest offensive
        resultat = df[kolonner].sort_values(by='xgi_per_90', ascending=False).head(antall)
        
        # Rund av
        resultat['total_vektet_forsvar_vurdering'] = resultat['total_vektet_forsvar_vurdering'].round(1)
        resultat['xgi_per_90'] = resultat['xgi_per_90'].round(2)
        resultat['expected_assists'] = resultat['expected_assists'].round(2)
        resultat['clean_sheet_potential'] = resultat['clean_sheet_potential'].round(1)
        resultat['bonus_per_kamp'] = resultat['bonus_per_kamp'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        return resultat
    
    def bygg_anbefalt_lag(self, budsjett=89.0):
        """
        Bygger et anbefalt lag basert på balansert tilnærming.
        Bruker en optimalisert fordeling av budsjettet for å få best mulig lagbalanse.
        """
        print(f"\n{'='*100}")
        print(f"BYGGER ANBEFALT LAG MED £{budsjett}m BUDSJETT")
        print(f"{'='*100}\n")
        
        valgt_lag = {
            'keepere': [],
            'forsvar': [],
            'midtbane': [],
            'angrep': []
        }
        
        brukt_budsjett = 0.0
        brukte_lag = set()  # For å unngå for mange spillere fra samme lag
        
        # Hent alle dataframes
        forsvar_df = self.beregn_avansert_forsvar_score()
        midtbane_df = self.beregn_avansert_midtbane_score()
        spisser_df = self.beregn_avansert_spiss_score()
        alle_spillere_df = self.beregn_metrics()
        
        if forsvar_df is None or midtbane_df is None or spisser_df is None:
            print("Kunne ikke bygge lag - mangler data")
            return None
        
        # Filtrer på spilletid
        forsvar_df = forsvar_df[forsvar_df['minutes'] >= 180].copy()
        midtbane_df = midtbane_df[midtbane_df['minutes'] >= 180].copy()
        spisser_df = spisser_df[spisser_df['minutes'] >= 180].copy()
        
        def velg_spillere(df, posisjon, antall, budsjett_guide, score_kolonne):
            """Velger spillere basert på score og budsjett"""
            valgte = []
            df_sorted = df.sort_values(by=score_kolonne, ascending=False)
            
            for _, spiller in df_sorted.iterrows():
                if len(valgte) >= antall:
                    break
                
                pris = spiller['pris_mill']
                lag = spiller['team']
                
                # Sjekk lagbegrensning (maks 3 spillere fra samme lag)
                lag_count = sum(1 for v in valgt_lag.values() for s in v if s['team'] == lag)
                if lag_count >= 3:
                    continue
                
                # Legg til spiller
                valgte.append({
                    'navn': spiller['web_name'],
                    'lag': spiller['lag_short'],
                    'team': lag,
                    'pris': pris,
                    'score': spiller[score_kolonne],
                    'form': spiller.get('form_num', 0),
                    'total_points': spiller.get('total_points', 0)
                })
            
            return valgte
        
        # 1. KEEPERE (2 stk, totalt £9.5m)
        # En keeper til £5.0m, en til £4.5m
        print("Velger keepere...")
        keepere_df = alle_spillere_df[alle_spillere_df['posisjon'] == 'GK'].copy()
        keepere_df = keepere_df[keepere_df['minutes'] >= 180].sort_values(by='ppm', ascending=False)
        
        # Første keeper (£4.5-5.5m)
        for _, keeper in keepere_df.iterrows():
            if 4.5 <= keeper['pris_mill'] <= 5.5:
                valgt_lag['keepere'].append({
                    'navn': keeper['web_name'],
                    'lag': keeper['lag_short'],
                    'team': keeper['team'],
                    'pris': keeper['pris_mill'],
                    'score': keeper['ppm'],
                    'form': keeper['form_num'],
                    'total_points': keeper['total_points']
                })
                brukt_budsjett += keeper['pris_mill']
                break
        
        # Andre keeper (£4.0-4.5m backup)
        for _, keeper in keepere_df.iterrows():
            if keeper['pris_mill'] <= 4.5 and keeper['team'] not in [k['team'] for k in valgt_lag['keepere']]:
                valgt_lag['keepere'].append({
                    'navn': keeper['web_name'],
                    'lag': keeper['lag_short'],
                    'team': keeper['team'],
                    'pris': keeper['pris_mill'],
                    'score': keeper['ppm'],
                    'form': keeper['form_num'],
                    'total_points': keeper['total_points']
                })
                brukt_budsjett += keeper['pris_mill']
                break
        
        print(f"✓ Keepere valgt: {len(valgt_lag['keepere'])} spillere, £{sum(k['pris'] for k in valgt_lag['keepere']):.1f}m brukt")
        
        # 2. FORSVAR (5 stk, totalt £24-25m)
        # 1x £6.0m, 1x £5.5m, 2x £5.0m, 1x £3.5-4.5m
        print("\nVelger forsvarsspillere...")
        forsvar_premium = forsvar_df[forsvar_df['pris_mill'] >= 5.5].head(2)
        forsvar_mid = forsvar_df[(forsvar_df['pris_mill'] >= 4.5) & (forsvar_df['pris_mill'] < 5.5)].head(2)
        forsvar_budget = forsvar_df[forsvar_df['pris_mill'] < 4.5].head(1)
        
        for df_subset in [forsvar_premium, forsvar_mid, forsvar_budget]:
            for _, spiller in df_subset.iterrows():
                if len(valgt_lag['forsvar']) >= 5:
                    break
                lag_count = sum(1 for v in valgt_lag.values() for s in v if s['team'] == spiller['team'])
                if lag_count >= 3:
                    continue
                valgt_lag['forsvar'].append({
                    'navn': spiller['web_name'],
                    'lag': spiller['lag_short'],
                    'team': spiller['team'],
                    'pris': spiller['pris_mill'],
                    'score': spiller['total_vektet_forsvar_vurdering'],
                    'form': spiller['form_num'],
                    'total_points': spiller['total_points']
                })
                brukt_budsjett += spiller['pris_mill']
        
        print(f"✓ Forsvar valgt: {len(valgt_lag['forsvar'])} spillere, £{sum(f['pris'] for f in valgt_lag['forsvar']):.1f}m brukt")
        
        # 3. MIDTBANE (5 stk, totalt £36-38m)
        # 1x premium £10m+, 2x £7-9m, 1x £6-7m, 1x £4.5m
        print("\nVelger midtbanespillere...")
        mid_premium = midtbane_df[midtbane_df['pris_mill'] >= 9.5].head(1)
        mid_high = midtbane_df[(midtbane_df['pris_mill'] >= 7.0) & (midtbane_df['pris_mill'] < 9.5)].head(2)
        mid_mid = midtbane_df[(midtbane_df['pris_mill'] >= 5.5) & (midtbane_df['pris_mill'] < 7.0)].head(1)
        mid_budget = midtbane_df[midtbane_df['pris_mill'] < 5.5].head(1)
        
        for df_subset in [mid_premium, mid_high, mid_mid, mid_budget]:
            for _, spiller in df_subset.iterrows():
                if len(valgt_lag['midtbane']) >= 5:
                    break
                lag_count = sum(1 for v in valgt_lag.values() for s in v if s['team'] == spiller['team'])
                if lag_count >= 3:
                    continue
                valgt_lag['midtbane'].append({
                    'navn': spiller['web_name'],
                    'lag': spiller['lag_short'],
                    'team': spiller['team'],
                    'pris': spiller['pris_mill'],
                    'score': spiller['total_vektet_midtbane_vurdering'],
                    'form': spiller['form_num'],
                    'total_points': spiller['total_points']
                })
                brukt_budsjett += spiller['pris_mill']
        
        print(f"✓ Midtbane valgt: {len(valgt_lag['midtbane'])} spillere, £{sum(m['pris'] for m in valgt_lag['midtbane']):.1f}m brukt")
        
        # 4. ANGREP (3 stk, totalt £18-20m)
        # 1x £7-8m, 1x £6-7m, 1x £4-5m
        print("\nVelger spisser...")
        spiss_high = spisser_df[spisser_df['pris_mill'] >= 7.0].head(1)
        spiss_mid = spisser_df[(spisser_df['pris_mill'] >= 5.5) & (spisser_df['pris_mill'] < 7.0)].head(1)
        spiss_budget = spisser_df[spisser_df['pris_mill'] < 5.5].head(1)
        
        for df_subset in [spiss_high, spiss_mid, spiss_budget]:
            for _, spiller in df_subset.iterrows():
                if len(valgt_lag['angrep']) >= 3:
                    break
                lag_count = sum(1 for v in valgt_lag.values() for s in v if s['team'] == spiller['team'])
                if lag_count >= 3:
                    continue
                valgt_lag['angrep'].append({
                    'navn': spiller['web_name'],
                    'lag': spiller['lag_short'],
                    'team': spiller['team'],
                    'pris': spiller['pris_mill'],
                    'score': spiller['total_vektet_spiss_vurdering'],
                    'form': spiller['form_num'],
                    'total_points': spiller['total_points']
                })
                brukt_budsjett += spiller['pris_mill']
        
        print(f"✓ Angrep valgt: {len(valgt_lag['angrep'])} spillere, £{sum(a['pris'] for a in valgt_lag['angrep']):.1f}m brukt")
        
        # Vis resultat
        print(f"\n{'='*100}")
        print(f"ANBEFALT LAG - BALANSERT TILNÆRMING")
        print(f"{'='*100}\n")
        
        print("KEEPERE:")
        print("-" * 80)
        for keeper in valgt_lag['keepere']:
            print(f"  {keeper['navn']:20s} ({keeper['lag']:3s}) - £{keeper['pris']:.1f}m | PPM: {keeper['score']:.2f} | Form: {keeper['form']:.1f}")
        
        print("\nFORSVAR:")
        print("-" * 80)
        for forsvar in valgt_lag['forsvar']:
            print(f"  {forsvar['navn']:20s} ({forsvar['lag']:3s}) - £{forsvar['pris']:.1f}m | Score: {forsvar['score']:.1f} | Form: {forsvar['form']:.1f}")
        
        print("\nMIDTBANE:")
        print("-" * 80)
        for mid in valgt_lag['midtbane']:
            print(f"  {mid['navn']:20s} ({mid['lag']:3s}) - £{mid['pris']:.1f}m | Score: {mid['score']:.1f} | Form: {mid['form']:.1f}")
        
        print("\nANGREP:")
        print("-" * 80)
        for angrep in valgt_lag['angrep']:
            print(f"  {angrep['navn']:20s} ({angrep['lag']:3s}) - £{angrep['pris']:.1f}m | Score: {angrep['score']:.1f} | Form: {angrep['form']:.1f}")
        
        print(f"\n{'='*100}")
        print(f"TOTAL PRIS: £{brukt_budsjett:.1f}m / £{budsjett:.1f}m")
        print(f"GJENSTÅENDE: £{budsjett - brukt_budsjett:.1f}m")
        print(f"ANTALL SPILLERE: {sum(len(v) for v in valgt_lag.values())}/15")
        print(f"{'='*100}\n")
        
        # Forslag til startoppstilling (beste 11)
        print("FORESLÅTT STARTOPPSTILLING (beste 11):")
        print("-" * 80)
        print("Formasjon: 3-4-3 eller 3-5-2 avhengig av fixtures\n")
        
        print("Keeper:")
        print(f"  {valgt_lag['keepere'][0]['navn']} ({valgt_lag['keepere'][0]['lag']})")
        
        print("\nForsvar (3 beste):")
        forsvar_sorted = sorted(valgt_lag['forsvar'], key=lambda x: x['score'], reverse=True)[:3]
        for f in forsvar_sorted:
            print(f"  {f['navn']} ({f['lag']})")
        
        print("\nMidtbane (4 beste):")
        mid_sorted = sorted(valgt_lag['midtbane'], key=lambda x: x['score'], reverse=True)[:4]
        for m in mid_sorted:
            print(f"  {m['navn']} ({m['lag']})")
        
        print("\nAngrep (2-3 beste avhengig av formasjon):")
        angrep_sorted = sorted(valgt_lag['angrep'], key=lambda x: x['score'], reverse=True)
        for a in angrep_sorted:
            print(f"  {a['navn']} ({a['lag']})")
        
        print(f"\n{'='*100}\n")
        
        return valgt_lag
    
    def rimelige_perler(self, maks_pris=6.0, min_minutter=180, antall=10):
        """Finner rimelige spillere med god verdi"""
        df = self.beregn_metrics()
        
        if df is None:
            return None
        
        # Filtrer på pris og spilletid
        df = df[(df['pris_mill'] <= maks_pris) & (df['minutes'] >= min_minutter)]
        
        kolonner = [
            'web_name', 'lag_navn', 'posisjon', 'pris_mill',
            'total_points', 'ppm', 'form_num', 'valgt_prosent'
        ]
        
        return df[kolonner].sort_values(by='ppm', ascending=False).head(antall)
    
    def vis_rapport(self):
        """Viser en forenklet rapport med topp 25 spillere per posisjon"""
        print("\n" + "="*100)
        print("FANTASY PREMIER LEAGUE - AVANSERT SPILLERANALYSE")
        print("="*100)
        
        # Vis tid til neste deadline
        self._vis_deadline_countdown()
        
        print("\n🧤 TOPP 15 KEEPERE - EXPECTED POINTS (xPts)")
        print("-"*100)
        print("xPts modell: 4*CS + 0.33*Saves + MinPts + Bonus (justert for fixtures og spilletid)")
        print("-"*100)
        keepere = self.beste_keepere(antall=15, min_minutter=180)
        if keepere is not None:
            print(keepere.to_string(index=False))
        
        print("\n\n⭐ TOPP 25 SPISSER - EXPECTED POINTS (xPts)")
        print("-"*100)
        print("xPts modell: 4*xG + 3*xA + MinPts + Bonus (justert for fixtures og spilletid)")
        print("-"*100)
        spisser = self.beste_spisser_avansert(antall=15, min_minutter=180)
        if spisser is not None:
            print(spisser.to_string(index=False))
        
        print("\n\n🎯 TOPP 25 MIDTBANESPILLERE - EXPECTED POINTS (xPts)")
        print("-"*100)
        print("xPts modell: 5*xG + 3*xA + 1*CS + MinPts + Bonus (justert for fixtures og spilletid)")
        print("-"*100)
        midtbane = self.beste_midtbanespillere(antall=15, min_minutter=180)
        if midtbane is not None:
            print(midtbane.to_string(index=False))
        
        print("\n\n🛡️ TOPP 25 FORSVARSSPILLERE - EXPECTED POINTS (xPts)")
        print("-"*100)
        print("xPts modell: 4*CS + 6*xG + 3*xA + MinPts + Bonus (justert for fixtures og spilletid)")
        print("-"*100)
        forsvar = self.beste_forsvarsspillere(antall=15, min_minutter=180)
        if forsvar is not None:
            print(forsvar.to_string(index=False))
        
        print("\n" + "="*100)
        
        # Vis drømmelaget basert på xPts
        self.vis_drommelag()
        
        # Vis mitt lag med rangeringer
        self.vis_mitt_lag(team_id=6740096)
    
    def vis_drommelag(self):
        """Viser det beste laget basert på xPts-rangeringer"""
        print(f"\n{'='*100}")
        print(f"🏆 UKENS DRØMMELAG - BASERT PÅ xPts ANALYSE")
        print(f"{'='*100}")
        
        try:
            # Finn forrige gameweek for poeng
            prev_gw = None
            current_gw = None
            for event in self.data.get('events', []):
                if event.get('is_current', False):
                    current_gw = event['id']
                    prev_gw = current_gw - 1 if current_gw > 1 else 1
                    break
            
            if current_gw is None:
                for event in reversed(self.data.get('events', [])):
                    if event.get('finished', False):
                        prev_gw = event['id']
                        break
            
            # Hent topp spillere fra hver posisjon
            spisser_df = self.beregn_avansert_spiss_score()
            midtbane_df = self.beregn_avansert_midtbane_score()
            forsvar_df = self.beregn_avansert_forsvar_score()
            
            # Filtrer og sorter
            spisser_df = spisser_df[spisser_df['minutes'] >= 180].sort_values(by='xPts_adjusted', ascending=False)
            midtbane_df = midtbane_df[midtbane_df['minutes'] >= 180].sort_values(by='xPts_adjusted', ascending=False)
            forsvar_df = forsvar_df[forsvar_df['minutes'] >= 180].sort_values(by='xPts_adjusted', ascending=False)
            
            # Finn Kelleher (Liverpool keeper)
            keeper = self.players_df[
                (self.players_df['web_name'] == 'Kelleher') & 
                (self.players_df['element_type'] == 1)
            ]
            
            if keeper.empty:
                # Fallback: beste keeper basert på poeng
                keeper = self.players_df[self.players_df['element_type'] == 1].sort_values(by='total_points', ascending=False).head(1)
            
            # Hent poeng fra forrige runde for hver spiller
            def get_prev_gw_points(player_id):
                try:
                    url = f"https://fantasy.premierleague.com/api/element-summary/{player_id}/"
                    response = requests.get(url, verify=False, timeout=5)
                    if response.status_code == 200:
                        data = response.json()
                        history = data.get('history', [])
                        if history:
                            # Siste kamp
                            return history[-1].get('total_points', 0)
                except:
                    pass
                return 0
            
            # Bygg drømmelaget
            dream_team = []
            
            # Keeper
            if not keeper.empty:
                k = keeper.iloc[0]
                team_name = self.teams_df[self.teams_df['id'] == k['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(k['id'])
                dream_team.append({
                    'pos': 'GK',
                    'name': k['web_name'],
                    'team': team_name,
                    'price': k['now_cost'] / 10,
                    'prev_pts': prev_pts,
                    'total_pts': k['total_points'],
                    'xPts': '-'
                })
            
            # 3 beste forsvarere
            for _, row in forsvar_df.head(3).iterrows():
                team_name = self.teams_df[self.teams_df['id'] == row['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(row['id'])
                dream_team.append({
                    'pos': 'DEF',
                    'name': row['web_name'],
                    'team': team_name,
                    'price': row['pris_mill'],
                    'prev_pts': prev_pts,
                    'total_pts': row['total_points'],
                    'xPts': round(row['xPts_adjusted'], 2)
                })
            
            # 4 beste midtbanespillere
            for _, row in midtbane_df.head(4).iterrows():
                team_name = self.teams_df[self.teams_df['id'] == row['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(row['id'])
                dream_team.append({
                    'pos': 'MID',
                    'name': row['web_name'],
                    'team': team_name,
                    'price': row['pris_mill'],
                    'prev_pts': prev_pts,
                    'total_pts': row['total_points'],
                    'xPts': round(row['xPts_adjusted'], 2)
                })
            
            # 3 beste spisser
            for _, row in spisser_df.head(3).iterrows():
                team_name = self.teams_df[self.teams_df['id'] == row['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(row['id'])
                dream_team.append({
                    'pos': 'FWD',
                    'name': row['web_name'],
                    'team': team_name,
                    'price': row['pris_mill'],
                    'prev_pts': prev_pts,
                    'total_pts': row['total_points'],
                    'xPts': round(row['xPts_adjusted'], 2)
                })
            
            # Vis tabellen
            print(f"\n{'Pos':<5} {'Spiller':<18} {'Lag':<5} {'Pris':<7} {'GW Pts':<8} {'Total':<8} {'xPts':<8}")
            print("-" * 75)
            
            total_prev = 0
            total_season = 0
            total_price = 0
            
            for player in dream_team:
                print(f"{player['pos']:<5} {player['name']:<18} {player['team']:<5} £{player['price']:<6.1f} {player['prev_pts']:<8} {player['total_pts']:<8} {player['xPts']:<8}")
                total_prev += player['prev_pts']
                total_season += player['total_pts']
                total_price += player['price']
            
            print("-" * 75)
            print(f"{'TOTAL':<5} {'':<18} {'':<5} £{total_price:<6.1f} {total_prev:<8} {total_season:<8}")
            print(f"\n{'='*100}")
            
        except Exception as e:
            print(f"Kunne ikke generere drømmelag: {e}")
    
    def vis_mitt_lag(self, team_id=6740096):
        """Henter og viser brukerens lag med rangeringer fra analysen"""
        print(f"\n{'='*100}")
        print(f"📋 MITT LAG - RANGERING BASERT PÅ ANALYSE")
        print(f"{'='*100}")
        
        # Hent lagets data fra FPL API
        try:
            # Finn nåværende gameweek
            current_gw = None
            for event in self.data.get('events', []):
                if event.get('is_current', False):
                    current_gw = event['id']
                    break
            
            if current_gw is None:
                # Finn siste finished gameweek
                for event in reversed(self.data.get('events', [])):
                    if event.get('finished', False):
                        current_gw = event['id']
                        break
            
            if current_gw is None:
                current_gw = 1
            
            # Hent lagets picks
            url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{current_gw}/picks/"
            response = requests.get(url, verify=False, timeout=10)
            
            if response.status_code != 200:
                print(f"Kunne ikke hente lag (status {response.status_code})")
                return None
            
            picks_data = response.json()
            picks = picks_data.get('picks', [])
            
            # Hent laginfo
            url_entry = f"https://fantasy.premierleague.com/api/entry/{team_id}/"
            response_entry = requests.get(url_entry, verify=False, timeout=10)
            if response_entry.status_code == 200:
                entry_data = response_entry.json()
                team_name = entry_data.get('name', 'Ukjent lag')
                print(f"\n🏆 Lag: {team_name}")
                print(f"   Gameweek: {current_gw}")
            
            # Hent rangeringer for hver posisjon
            print("\nHenter rangeringer...")
            spisser_df = self.beregn_avansert_spiss_score()
            midtbane_df = self.beregn_avansert_midtbane_score()
            forsvar_df = self.beregn_avansert_forsvar_score()
            
            # Sorter for å få rangeringer
            spisser_df = spisser_df.sort_values(by='total_vektet_spiss_vurdering', ascending=False).reset_index(drop=True)
            midtbane_df = midtbane_df.sort_values(by='total_vektet_midtbane_vurdering', ascending=False).reset_index(drop=True)
            forsvar_df = forsvar_df.sort_values(by='xPts_adjusted', ascending=False).reset_index(drop=True)
            
            # Lag en funksjon for rask oppslag
            def get_rank(df, player_id, score_col):
                player_row = df[df['id'] == player_id]
                if player_row.empty:
                    return None, None
                idx = df[df['id'] == player_id].index[0]
                score = player_row[score_col].values[0]
                return idx + 1, score
            
            print(f"\n{'─'*80}")
            print(f"{'Pos':<5} {'Spiller':<20} {'Lag':<5} {'Pris':<7} {'Rangering':<12} {'Score':<10}")
            print(f"{'─'*80}")
            
            startere = []
            benk = []
            
            for pick in picks:
                player_id = pick['element']
                position = pick['position']  # 1-11 = startere, 12-15 = benk
                is_captain = pick['is_captain']
                is_vice = pick['is_vice_captain']
                
                # Finn spillerinfo
                player_info = self.players_df[self.players_df['id'] == player_id]
                if player_info.empty:
                    continue
                
                player = player_info.iloc[0]
                name = player['web_name']
                team = self.teams_df[self.teams_df['id'] == player['team']].iloc[0]['short_name']
                price = player['now_cost'] / 10
                pos_type = ['GKP', 'DEF', 'MID', 'FWD'][player['element_type'] - 1]
                
                # Finn rangering basert på posisjon
                if pos_type == 'FWD':
                    rank, score = get_rank(spisser_df, player_id, 'total_vektet_spiss_vurdering')
                    total_in_pos = len(spisser_df)
                elif pos_type == 'MID':
                    rank, score = get_rank(midtbane_df, player_id, 'total_vektet_midtbane_vurdering')
                    total_in_pos = len(midtbane_df)
                elif pos_type == 'DEF':
                    rank, score = get_rank(forsvar_df, player_id, 'xPts_adjusted')
                    total_in_pos = len(forsvar_df)
                else:  # GKP
                    rank, score = None, None
                    total_in_pos = 0
                
                # Marker kaptein/vice
                captain_mark = " (C)" if is_captain else " (V)" if is_vice else ""
                
                # Lag rangering-tekst
                if rank:
                    rank_text = f"#{rank}/{total_in_pos}"
                    rank_emoji = "🥇" if rank <= 3 else "🥈" if rank <= 10 else "🥉" if rank <= 25 else "⚪"
                    score_text = f"{score:.1f}"
                else:
                    rank_text = "N/A"
                    rank_emoji = "⚪"
                    score_text = "N/A"
                
                player_data = {
                    'position': position,
                    'pos_type': pos_type,
                    'name': name + captain_mark,
                    'team': team,
                    'price': price,
                    'rank': rank,
                    'rank_text': rank_text,
                    'rank_emoji': rank_emoji,
                    'score_text': score_text
                }
                
                if position <= 11:
                    startere.append(player_data)
                else:
                    benk.append(player_data)
            
            # Vis startere
            print("\n⚽ STARTERE:")
            for p in startere:
                print(f"{p['pos_type']:<5} {p['name']:<20} {p['team']:<5} £{p['price']:<6.1f} {p['rank_emoji']} {p['rank_text']:<10} {p['score_text']:<10}")
            
            # Vis benk
            print(f"\n{'─'*80}")
            print("🪑 BENK:")
            for p in benk:
                print(f"{p['pos_type']:<5} {p['name']:<20} {p['team']:<5} £{p['price']:<6.1f} {p['rank_emoji']} {p['rank_text']:<10} {p['score_text']:<10}")
            
            print(f"{'─'*80}")
            
            # Oppsummering
            ranks = [p['rank'] for p in startere + benk if p['rank'] is not None]
            if ranks:
                avg_rank = sum(ranks) / len(ranks)
                top_10 = sum(1 for r in ranks if r <= 10)
                print(f"\n📊 OPPSUMMERING:")
                print(f"   Gjennomsnittlig rangering: #{avg_rank:.1f}")
                print(f"   Spillere i topp 10: {top_10}")
                print(f"   Spillere i topp 25: {sum(1 for r in ranks if r <= 25)}")
            
            print(f"\n{'='*100}")
            
        except Exception as e:
            print(f"Feil ved henting av lag: {e}")
            return None
    
    def _vis_deadline_countdown(self):
        """Viser countdown til neste transfer deadline"""
        if self.data is None:
            return
        
        try:
            # Finn neste gameweek som ikke er ferdig
            events = self.data.get('events', [])
            neste_gw = None
            
            for event in events:
                if not event.get('finished', True):
                    neste_gw = event
                    break
            
            if neste_gw is None:
                print("\n⚠️ Ingen kommende gameweek funnet")
                return
            
            # Parse deadline tid
            deadline_str = neste_gw.get('deadline_time', '')
            if deadline_str:
                deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                nå = datetime.now(deadline.tzinfo)
                
                tid_igjen = deadline - nå
                
                if tid_igjen.total_seconds() > 0:
                    dager = tid_igjen.days
                    timer = tid_igjen.seconds // 3600
                    minutter = (tid_igjen.seconds % 3600) // 60
                    
                    print(f"\n⏰ TRANSFER DEADLINE - GAMEWEEK {neste_gw.get('id', '?')}")
                    print(f"   Deadline: {deadline.strftime('%A %d. %B %Y kl. %H:%M')}")
                    print(f"   Tid igjen: {dager} dager, {timer} timer, {minutter} minutter")
                    
                    if dager == 0 and timer < 6:
                        print(f"   ⚠️ HASTER! Mindre enn 6 timer til deadline!")
                    elif dager == 0:
                        print(f"   ⚠️ Deadline er I DAG!")
                    elif dager == 1:
                        print(f"   📅 Deadline er I MORGEN!")
                else:
                    print(f"\n⏰ Gameweek {neste_gw.get('id', '?')} deadline har passert")
                    
        except Exception as e:
            print(f"\n⚠️ Kunne ikke hente deadline-info: {e}")
    
    def generer_html_rapport(self, filnavn="Fantasy_Premier_League_recommendations.html"):
        """Genererer en pen HTML-rapport med styling og ikoner"""
        
        # Hent data
        keepere = self.beste_keepere(antall=15, min_minutter=180)
        spisser = self.beste_spisser_avansert(antall=15, min_minutter=180)
        midtbane = self.beste_midtbanespillere(antall=15, min_minutter=180)
        forsvar = self.beste_forsvarsspillere(antall=15, min_minutter=180)
        
        # Hent mitt lag data
        mitt_lag_html = self._get_mitt_lag_html(team_id=6740096)
        
        # Hent value for money HTML
        value_for_money_html = self._get_value_for_money_html()
        
        # Hent drømmelag HTML
        drommelag_html = self._get_drommelag_html()
        
        # Hent deadline info
        deadline_html = self._get_deadline_html()
        
        html = f'''<!DOCTYPE html>
<html lang="no">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FPL Analyse</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #00ff87;
            --primary-dark: #00d972;
            --dark: #0d1117;
            --darker: #010409;
            --card-bg: #161b22;
            --border: #30363d;
            --text: #e6edf3;
            --text-muted: #8b949e;
        }}
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Outfit', -apple-system, sans-serif;
            background: var(--darker);
            min-height: 100vh;
            padding: 20px;
            color: var(--text);
            line-height: 1.6;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            padding: 40px 20px;
            background: var(--card-bg);
            border-radius: 16px;
            margin-bottom: 24px;
            border: 1px solid var(--border);
        }}
        .header h1 {{
            font-size: 2.2em;
            margin-bottom: 8px;
            color: var(--text);
            font-weight: 800;
        }}
        .header h1 span {{
            color: var(--primary);
        }}
        .header .subtitle {{
            font-size: 1.1em;
            color: var(--text-muted);
        }}
        .deadline-box {{
            background: var(--card-bg);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
            text-align: center;
            border: 1px solid var(--primary);
        }}
        .deadline-box h2 {{
            font-size: 1.5em;
            margin-bottom: 10px;
            color: var(--primary);
        }}
        .deadline-box .time {{
            font-size: 2em;
            font-weight: bold;
            color: var(--text);
        }}
        .deadline-box.urgent {{
            animation: pulse 1s infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.02); }}
        }}
        .section {{
            background: var(--card-bg);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid var(--border);
        }}
        .section-header {{
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }}
        .section-icon {{
            font-size: 2em;
        }}
        .section-title {{
            font-size: 1.4em;
            color: var(--text);
            font-weight: 700;
        }}
        .section-desc {{
            font-size: 0.85em;
            color: var(--text-muted);
            margin-top: 4px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9em;
            background: var(--dark);
            border-radius: 12px;
            overflow: hidden;
        }}
        th {{
            background: var(--border);
            color: var(--text);
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
            position: sticky;
            top: 0;
        }}
        td {{
            padding: 10px 8px;
            border-bottom: 1px solid var(--border);
            color: var(--text);
            background: var(--dark);
        }}
        tr:hover td {{
            background: rgba(0, 255, 135, 0.05);
        }}
        tr:nth-child(1) td {{ background: rgba(255, 215, 0, 0.15); }}
        tr:nth-child(2) td {{ background: rgba(192, 192, 192, 0.1); }}
        tr:nth-child(3) td {{ background: rgba(205, 127, 50, 0.1); }}
        .rank {{
            font-weight: bold;
            color: var(--text-muted);
        }}
        .player-name {{
            font-weight: 600;
            color: var(--text);
        }}
        .team-badge {{
            background: var(--border);
            color: var(--text-muted);
            padding: 3px 8px;
            border-radius: 5px;
            font-size: 0.85em;
            font-weight: 600;
        }}
        .price {{
            color: var(--primary);
            font-weight: 600;
        }}
        .score {{
            background: var(--primary);
            color: var(--dark);
            padding: 5px 10px;
            border-radius: 8px;
            font-weight: bold;
        }}
        .footer {{
            text-align: center;
            padding: 30px;
            color: var(--text-muted);
            font-size: 0.9em;
        }}
        .highlight-box {{
            background: var(--card-bg);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
            border: 1px solid rgba(255, 107, 107, 0.3);
        }}
        .highlight-box h3 {{
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .medal {{
            display: inline-block;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            text-align: center;
            line-height: 24px;
            font-weight: bold;
            font-size: 0.8em;
        }}
        .gold {{ background: linear-gradient(135deg, #ffd700, #ffec8b); color: #1a1a2e; }}
        .silver {{ background: linear-gradient(135deg, #c0c0c0, #e8e8e8); color: #1a1a2e; }}
        .bronze {{ background: linear-gradient(135deg, #cd7f32, #daa06d); color: #1a1a2e; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚽ FPL <span>Analyse</span></h1>
            <div class="subtitle">AI-drevet innsikt og anbefalinger for ditt lag</div>
        </div>
        
        {deadline_html}
        
        {mitt_lag_html}
        
        {value_for_money_html}
        
        {drommelag_html}
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🧤</span>
                <div>
                    <div class="section-title">Top 15 Goalkeepers - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 4×CS + 0.33×Saves + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(keepere, 'GKP')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🛡️</span>
                <div>
                    <div class="section-title">Top 15 Defenders - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 4×CS + 6×xG + 3×xA + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(forsvar, 'DEF')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🎯</span>
                <div>
                    <div class="section-title">Top 15 Midfielders - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 5×xG + 3×xA + 1×CS + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(midtbane, 'MID')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">⭐</span>
                <div>
                    <div class="section-title">Top 15 Forwards - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 4×xG + 3×xA + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(spisser, 'FWD')}
        </div>
        
        <div class="footer">
            <p>FPL Analyse • AI-drevet innsikt for Fantasy Premier League</p>
            <p>Rapport generert: {datetime.now().strftime('%d.%m.%Y kl. %H:%M')}</p>
            <p style="margin-top: 15px;"><a href="https://fplanalyse.no/unsubscribe.html">Meld deg av tjenesten</a></p>
        </div>
    </div>
</body>
</html>'''
        
        # Skriv til fil
        with open(filnavn, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"✓ HTML-rapport generert: {filnavn}")
        return filnavn
    
    def _get_deadline_html(self):
        """Genererer HTML for deadline-boksen"""
        if self.data is None:
            return ""
        
        try:
            events = self.data.get('events', [])
            neste_gw = None
            
            for event in events:
                if not event.get('finished', True):
                    neste_gw = event
                    break
            
            if neste_gw is None:
                return ""
            
            deadline_str = neste_gw.get('deadline_time', '')
            if deadline_str:
                deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                nå = datetime.now(deadline.tzinfo)
                tid_igjen = deadline - nå
                
                if tid_igjen.total_seconds() > 0:
                    dager = tid_igjen.days
                    timer = tid_igjen.seconds // 3600
                    minutter = (tid_igjen.seconds % 3600) // 60
                    
                    urgent_class = "urgent" if dager == 0 and timer < 6 else ""
                    warning = ""
                    if dager == 0 and timer < 6:
                        warning = "<div style='margin-top:10px;font-size:1.2em;'>⚠️ HURRY! Less than 6 hours remaining!</div>"
                    elif dager == 0:
                        warning = "<div style='margin-top:10px;font-size:1.2em;'>⚠️ Deadline is TODAY!</div>"
                    elif dager == 1:
                        warning = "<div style='margin-top:10px;font-size:1.2em;'>📅 Deadline is TOMORROW!</div>"
                    
                    return f'''
                    <div class="deadline-box {urgent_class}">
                        <h2>⏰ Transfer Deadline - Gameweek {neste_gw.get('id', '?')}</h2>
                        <div class="time">{dager}d {timer}h {minutter}m</div>
                        <div>{deadline.strftime('%A %d %B %Y at %H:%M')}</div>
                        {warning}
                    </div>'''
        except:
            pass
        return ""
    
    def _get_combined_greeting_deadline_html(self, subscriber_name):
        """Genererer kombinert greeting og deadline boks"""
        if self.data is None:
            return ""
        
        try:
            events = self.data.get('events', [])
            neste_gw = None
            
            for event in events:
                if not event.get('finished', True):
                    neste_gw = event
                    break
            
            if neste_gw is None:
                # Returner bare greeting uten deadline
                return f'''
                <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #161b22; border-radius: 16px; margin-bottom: 24px; border: 1px solid #30363d;">
                    <tr>
                        <td style="padding: 25px;">
                            <div style="font-size: 1.4em; font-weight: bold; color: #00ff87;">👋 Hei {subscriber_name}! Her er din personlige FPL-rapport</div>
                        </td>
                    </tr>
                </table>'''
            
            deadline_str = neste_gw.get('deadline_time', '')
            if deadline_str:
                deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                nå = datetime.now(deadline.tzinfo)
                tid_igjen = deadline - nå
                
                if tid_igjen.total_seconds() > 0:
                    dager = tid_igjen.days
                    timer = tid_igjen.seconds // 3600
                    minutter = (tid_igjen.seconds % 3600) // 60
                    
                    # Sjekk om deadline er samme dag som i dag
                    deadline_date = deadline.date()
                    today_date = nå.date()
                    tomorrow_date = (nå + timedelta(days=1)).date()
                    
                    warning_html = ""
                    if dager == 0 and timer < 6:
                        warning_html = '<td style="vertical-align: middle;"><div style="background-color: rgba(255,107,107,0.2); color: #ff6b6b; padding: 10px 20px; border-radius: 10px; font-weight: bold; border: 1px solid rgba(255,107,107,0.3);">⚠️ Bare noen timer igjen!</div></td>'
                    elif deadline_date == today_date:
                        warning_html = '<td style="vertical-align: middle;"><div style="background-color: rgba(255,107,107,0.2); color: #ff6b6b; padding: 10px 20px; border-radius: 10px; font-weight: bold; border: 1px solid rgba(255,107,107,0.3);">⚠️ Deadline er i dag!</div></td>'
                    elif deadline_date == tomorrow_date:
                        warning_html = '<td style="vertical-align: middle;"><div style="background-color: rgba(255,193,7,0.2); color: #ffc107; padding: 10px 20px; border-radius: 10px; font-weight: bold; border: 1px solid rgba(255,193,7,0.3);">📅 Deadline er i morgen!</div></td>'
                    
                    return f'''
                    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #161b22; border-radius: 16px; margin-bottom: 24px; border: 1px solid #30363d;">
                        <tr>
                            <td style="padding: 25px;">
                                <!-- Greeting -->
                                <div style="font-size: 1.4em; font-weight: bold; color: #00ff87; margin-bottom: 15px;">👋 Hei {subscriber_name}!</div>
                                
                                <!-- Deadline row -->
                                <table width="100%" cellpadding="0" cellspacing="0" style="border-top: 1px solid #30363d; padding-top: 15px;">
                                    <tr>
                                        <td style="vertical-align: middle; padding-top: 15px;">
                                            <table cellpadding="0" cellspacing="0">
                                                <tr>
                                                    <td style="font-size: 2.5em; vertical-align: middle; padding-right: 15px;">⏰</td>
                                                    <td style="vertical-align: middle;">
                                                        <div style="font-size: 1.1em; font-weight: bold; color: #8b949e;">Gameweek {neste_gw.get('id', '?')} Deadline</div>
                                                        <div style="font-size: 1.8em; font-weight: bold; color: #00ff87;">{dager}d {timer}t {minutter}m</div>
                                                        <div style="font-size: 0.95em; color: #8b949e;">{deadline.strftime('%A %d. %B %H:%M')}</div>
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                        {warning_html}
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>'''
        except:
            pass
        
        return f'''
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #161b22; border-radius: 16px; margin-bottom: 24px; border: 1px solid #30363d;">
            <tr>
                <td style="padding: 25px;">
                    <div style="font-size: 1.4em; font-weight: bold; color: #00ff87;">👋 Hei {subscriber_name}! Her er din personlige FPL-rapport</div>
                </td>
            </tr>
        </table>'''
    
    def _get_value_for_money_html(self):
        """Genererer HTML for Value for Money-tabellen - én spiller per posisjon"""
        try:
            # Hent topp spillere fra hver posisjon
            spisser_df = self.beregn_avansert_spiss_score()
            midtbane_df = self.beregn_avansert_midtbane_score()
            forsvar_df = self.beregn_avansert_forsvar_score()
            keeper_df = self.beregn_avansert_keeper_score()
            
            # Filtrer spillere med nok spilletid
            spisser_df = spisser_df[spisser_df['minutes'] >= 180].copy()
            midtbane_df = midtbane_df[midtbane_df['minutes'] >= 180].copy()
            forsvar_df = forsvar_df[forsvar_df['minutes'] >= 180].copy()
            keeper_df = keeper_df[keeper_df['minutes'] >= 180].copy()
            
            # Beregn value for money (xPts per million)
            spisser_df['value'] = spisser_df['xPts_adjusted'] / spisser_df['pris_mill']
            midtbane_df['value'] = midtbane_df['xPts_adjusted'] / midtbane_df['pris_mill']
            forsvar_df['value'] = forsvar_df['xPts_adjusted'] / forsvar_df['pris_mill']
            keeper_df['value'] = keeper_df['xPts_adjusted'] / keeper_df['pris_mill']
            
            # Legg til posisjon
            spisser_df['pos'] = 'FWD'
            midtbane_df['pos'] = 'MID'
            forsvar_df['pos'] = 'DEF'
            keeper_df['pos'] = 'GKP'
            
            # Finn beste value for hver posisjon
            best_keeper = keeper_df.sort_values(by='value', ascending=False).head(1)
            best_forsvar = forsvar_df.sort_values(by='value', ascending=False).head(1)
            best_midtbane = midtbane_df.sort_values(by='value', ascending=False).head(1)
            best_spiss = spisser_df.sort_values(by='value', ascending=False).head(1)
            
            # Kombiner i rekkefølge: GKP, DEF, MID, FWD
            top_value = pd.concat([best_keeper, best_forsvar, best_midtbane, best_spiss])
            
            # Bygg HTML
            rows_html = ""
            for _, row in top_value.iterrows():
                team_name = self.teams_df[self.teams_df['id'] == row['team']].iloc[0]['short_name']
                
                # Posisjons-farger
                pos_colors = {
                    'GKP': 'background-color: #ffcc00; color: #000;',
                    'DEF': 'background-color: #00ff87; color: #000;',
                    'MID': 'background-color: #00bfff; color: #000;',
                    'FWD': 'background-color: #ff6b6b; color: #fff;'
                }
                pos_style = pos_colors.get(row['pos'], 'background-color: #ccc; color: #000;')
                
                rows_html += f'''
                <tr>
                    <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117;"><span style="{pos_style} padding: 3px 8px; border-radius: 5px; font-size: 0.8em; font-weight: bold;">{row['pos']}</span></td>
                    <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117; font-weight: 600; color: #e6edf3;">{row['web_name']}</td>
                    <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117;"><span style="background-color: #30363d; color: #8b949e; padding: 2px 6px; border-radius: 4px; font-size: 0.85em;">{team_name}</span></td>
                    <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117; color: #00ff87; font-weight: 600;">£{row['pris_mill']:.1f}m</td>
                    <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117; font-weight: bold; color: #00ff87;">{row['xPts_adjusted']:.2f}</td>
                    <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117; font-weight: bold; color: #ffd700;">{row['value']:.2f}</td>
                </tr>'''
            
            html = f'''
        <table width="100%" cellpadding="0" cellspacing="0" style="background: linear-gradient(135deg, rgba(0,255,135,0.1) 0%, rgba(0,217,114,0.1) 100%); border-radius: 16px; margin-bottom: 24px; border: 1px solid rgba(0,255,135,0.3);">
            <tr>
                <td style="padding: 25px;">
                    <div style="margin-bottom: 20px;">
                        <span style="font-size: 2em; margin-right: 15px;">💰</span>
                        <span style="font-size: 1.5em; font-weight: bold; color: #00ff87;">Beste Value for Money</span>
                        <div style="color: #8b949e; font-size: 0.9em; margin-top: 5px; margin-left: 55px;">Høyest xPts per million (xPts/£m) per posisjon</div>
                    </div>
                    
                    <table width="100%" cellpadding="0" cellspacing="0" style="background: #0d1117; border-radius: 12px; border-collapse: collapse;">
                        <thead>
                            <tr>
                                <th style="background-color: #30363d; color: #e6edf3; padding: 12px 8px; text-align: left; font-weight: 600;">Pos</th>
                                <th style="background-color: #30363d; color: #e6edf3; padding: 12px 8px; text-align: left; font-weight: 600;">Spiller</th>
                                <th style="background-color: #30363d; color: #e6edf3; padding: 12px 8px; text-align: left; font-weight: 600;">Lag</th>
                                <th style="background-color: #30363d; color: #e6edf3; padding: 12px 8px; text-align: left; font-weight: 600;">Pris</th>
                                <th style="background-color: #30363d; color: #e6edf3; padding: 12px 8px; text-align: left; font-weight: 600;">xPts</th>
                                <th style="background-color: #30363d; color: #e6edf3; padding: 12px 8px; text-align: left; font-weight: 600;">xPts/£m</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows_html}
                        </tbody>
                    </table>
                </td>
            </tr>
        </table>'''
            
            return html
            
        except Exception as e:
            print(f"Feil ved generering av value for money: {e}")
            return ""
    
    def _get_drommelag_html(self):
        """Genererer HTML for drømmelaget"""
        try:
            # Finn forrige gameweek for poeng
            prev_gw = None
            for event in self.data.get('events', []):
                if event.get('is_current', False):
                    prev_gw = event['id'] - 1 if event['id'] > 1 else 1
                    break
            
            if prev_gw is None:
                for event in reversed(self.data.get('events', [])):
                    if event.get('finished', False):
                        prev_gw = event['id']
                        break
            
            # Hent topp spillere fra hver posisjon
            spisser_df = self.beregn_avansert_spiss_score()
            midtbane_df = self.beregn_avansert_midtbane_score()
            forsvar_df = self.beregn_avansert_forsvar_score()
            keeper_df = self.beregn_avansert_keeper_score()
            
            spisser_df = spisser_df[spisser_df['minutes'] >= 180].sort_values(by='xPts_adjusted', ascending=False)
            midtbane_df = midtbane_df[midtbane_df['minutes'] >= 180].sort_values(by='xPts_adjusted', ascending=False)
            forsvar_df = forsvar_df[forsvar_df['minutes'] >= 180].sort_values(by='xPts_adjusted', ascending=False)
            keeper_df = keeper_df[keeper_df['minutes'] >= 180].sort_values(by='xPts_adjusted', ascending=False)
            
            def get_prev_gw_points(player_id):
                try:
                    url = f"https://fantasy.premierleague.com/api/element-summary/{player_id}/"
                    response = requests.get(url, verify=False, timeout=5)
                    if response.status_code == 200:
                        data = response.json()
                        history = data.get('history', [])
                        if history:
                            return history[-1].get('total_points', 0)
                except:
                    pass
                return 0
            
            # Bygg drømmelaget
            rows_html = ""
            total_prev = 0
            total_season = 0
            total_price = 0
            
            # Keeper - bruk beste keeper basert på xPts
            if not keeper_df.empty:
                k = keeper_df.iloc[0]
                team_name = self.teams_df[self.teams_df['id'] == k['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(k['id'])
                total_prev += prev_pts
                total_season += k['total_points']
                total_price += k['pris_mill']
                rows_html += f'''
                <tr>
                    <td><span class="pos-badge pos-gkp">GK</span></td>
                    <td class="player-name">{k['web_name']}</td>
                    <td><span class="team-badge">{team_name}</span></td>
                    <td class="price">£{k['pris_mill']:.1f}m</td>
                    <td>{prev_pts}</td>
                    <td>{k['total_points']}</td>
                    <td><span class="xpts-badge">{k['xPts_adjusted']:.2f}</span></td>
                </tr>'''
            
            # 3 forsvarere
            for _, row in forsvar_df.head(3).iterrows():
                team_name = self.teams_df[self.teams_df['id'] == row['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(row['id'])
                total_prev += prev_pts
                total_season += row['total_points']
                total_price += row['pris_mill']
                rows_html += f'''
                <tr>
                    <td><span class="pos-badge pos-def">DEF</span></td>
                    <td class="player-name">{row['web_name']}</td>
                    <td><span class="team-badge">{team_name}</span></td>
                    <td class="price">£{row['pris_mill']:.1f}m</td>
                    <td>{prev_pts}</td>
                    <td>{row['total_points']}</td>
                    <td><span class="xpts-badge">{row['xPts_adjusted']:.2f}</span></td>
                </tr>'''
            
            # 4 midtbanespillere
            for _, row in midtbane_df.head(4).iterrows():
                team_name = self.teams_df[self.teams_df['id'] == row['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(row['id'])
                total_prev += prev_pts
                total_season += row['total_points']
                total_price += row['pris_mill']
                rows_html += f'''
                <tr>
                    <td><span class="pos-badge pos-mid">MID</span></td>
                    <td class="player-name">{row['web_name']}</td>
                    <td><span class="team-badge">{team_name}</span></td>
                    <td class="price">£{row['pris_mill']:.1f}m</td>
                    <td>{prev_pts}</td>
                    <td>{row['total_points']}</td>
                    <td><span class="xpts-badge">{row['xPts_adjusted']:.2f}</span></td>
                </tr>'''
            
            # 3 spisser
            for _, row in spisser_df.head(3).iterrows():
                team_name = self.teams_df[self.teams_df['id'] == row['team']].iloc[0]['short_name']
                prev_pts = get_prev_gw_points(row['id'])
                total_prev += prev_pts
                total_season += row['total_points']
                total_price += row['pris_mill']
                rows_html += f'''
                <tr>
                    <td><span class="pos-badge pos-fwd">FWD</span></td>
                    <td class="player-name">{row['web_name']}</td>
                    <td><span class="team-badge">{team_name}</span></td>
                    <td class="price">£{row['pris_mill']:.1f}m</td>
                    <td>{prev_pts}</td>
                    <td>{row['total_points']}</td>
                    <td><span class="xpts-badge">{row['xPts_adjusted']:.2f}</span></td>
                </tr>'''
            
            html = f'''
        <div class="section dream-team-section">
            <div class="section-header">
                <span class="section-icon">🏆</span>
                <div>
                    <div class="section-title">Ukens Drømmelag</div>
                    <div class="section-desc">Beste lag basert på xPts-analyse (3-4-3 formasjon)</div>
                </div>
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th>Pos</th>
                        <th>Spiller</th>
                        <th>Lag</th>
                        <th>Pris</th>
                        <th>GW Pts</th>
                        <th>Total</th>
                        <th>xPts</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
                <tfoot>
                    <tr class="total-row">
                        <td colspan="3"><strong>TOTAL</strong></td>
                        <td class="price"><strong>£{total_price:.1f}m</strong></td>
                        <td><strong>{total_prev}</strong></td>
                        <td><strong>{total_season}</strong></td>
                        <td></td>
                    </tr>
                </tfoot>
            </table>
        </div>
        
        <style>
            .dream-team-section {{
                background: linear-gradient(135deg, rgba(255, 215, 0, 0.1) 0%, rgba(255, 140, 0, 0.1) 100%);
                border: 1px solid rgba(255, 215, 0, 0.3);
            }}
            .dream-team-section .section-title {{
                color: #ffd700;
            }}
            .dream-team-section .section-desc {{
                color: #8b949e;
            }}
            .dream-team-section table {{
                background: #0d1117;
            }}
            .dream-team-section th {{
                background: rgba(255, 215, 0, 0.2) !important;
                color: #ffd700 !important;
            }}
            .dream-team-section td {{
                color: #e6edf3;
                background: #0d1117;
            }}
            .dream-team-section .total-row {{
                background: #161b22;
            }}
            .dream-team-section .total-row td {{
                border-top: 1px solid #ffd700;
                background: #161b22;
            }}
            .xpts-badge {{
                background: #00ff87;
                color: #0d1117;
                padding: 3px 8px;
                border-radius: 5px;
                font-weight: bold;
            }}
        </style>'''
            
            return html
            
        except Exception as e:
            return f"<!-- Error generating dream team: {e} -->"
    
    def _get_mitt_lag_html(self, team_id=6740096):
        """Genererer HTML for mitt lag-seksjonen"""
        try:
            # Finn nåværende gameweek
            current_gw = None
            for event in self.data.get('events', []):
                if event.get('is_current', False):
                    current_gw = event['id']
                    break
            
            if current_gw is None:
                for event in reversed(self.data.get('events', [])):
                    if event.get('finished', False):
                        current_gw = event['id']
                        break
            
            if current_gw is None:
                current_gw = 1
            
            # Hent lagets picks
            url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{current_gw}/picks/"
            response = requests.get(url, verify=False, timeout=10)
            
            if response.status_code != 200:
                return ""
            
            picks_data = response.json()
            picks = picks_data.get('picks', [])
            
            # Hent laginfo
            team_name = "Mitt Lag"
            url_entry = f"https://fantasy.premierleague.com/api/entry/{team_id}/"
            response_entry = requests.get(url_entry, verify=False, timeout=10)
            if response_entry.status_code == 200:
                entry_data = response_entry.json()
                team_name = entry_data.get('name', 'Mitt Lag')
            
            # Hent rangeringer
            spisser_df = self.beregn_avansert_spiss_score()
            midtbane_df = self.beregn_avansert_midtbane_score()
            forsvar_df = self.beregn_avansert_forsvar_score()
            keeper_df = self.beregn_avansert_keeper_score()
            
            spisser_df = spisser_df.sort_values(by='total_vektet_spiss_vurdering', ascending=False).reset_index(drop=True)
            midtbane_df = midtbane_df.sort_values(by='total_vektet_midtbane_vurdering', ascending=False).reset_index(drop=True)
            forsvar_df = forsvar_df.sort_values(by='xPts_adjusted', ascending=False).reset_index(drop=True)
            keeper_df = keeper_df.sort_values(by='xPts_adjusted', ascending=False).reset_index(drop=True)
            
            def get_rank(df, player_id, score_col):
                player_row = df[df['id'] == player_id]
                if player_row.empty:
                    return None, None
                idx = df[df['id'] == player_id].index[0]
                score = player_row[score_col].values[0]
                return idx + 1, score
            
            # Bygg spillerliste og samle data for anbefalinger
            startere_html = ""
            benk_html = ""
            all_ranks = []
            my_players = []  # For transfer-anbefalinger
            my_player_ids = set()  # For å unngå å anbefale spillere du allerede har
            
            for pick in picks:
                player_id = pick['element']
                position = pick['position']
                is_captain = pick['is_captain']
                is_vice = pick['is_vice_captain']
                my_player_ids.add(player_id)
                
                player_info = self.players_df[self.players_df['id'] == player_id]
                if player_info.empty:
                    continue
                
                player = player_info.iloc[0]
                name = player['web_name']
                player_team_id = player['team']
                team = self.teams_df[self.teams_df['id'] == player_team_id].iloc[0]['short_name']
                price = player['now_cost'] / 10
                pos_type = ['GKP', 'DEF', 'MID', 'FWD'][player['element_type'] - 1]
                
                if pos_type == 'FWD':
                    rank, score = get_rank(spisser_df, player_id, 'total_vektet_spiss_vurdering')
                    total_in_pos = len(spisser_df)
                elif pos_type == 'MID':
                    rank, score = get_rank(midtbane_df, player_id, 'total_vektet_midtbane_vurdering')
                    total_in_pos = len(midtbane_df)
                elif pos_type == 'DEF':
                    rank, score = get_rank(forsvar_df, player_id, 'xPts_adjusted')
                    total_in_pos = len(forsvar_df)
                elif pos_type == 'GKP':
                    rank, score = get_rank(keeper_df, player_id, 'xPts_adjusted')
                    total_in_pos = len(keeper_df)
                else:
                    rank, score = None, None
                    total_in_pos = 0
                
                # Lagre spillerdata for anbefalinger (kun startere)
                if position <= 11 and rank and score:
                    my_players.append({
                        'id': player_id,
                        'name': name,
                        'team': team,
                        'pos_type': pos_type,
                        'price': price,
                        'rank': rank,
                        'score': score
                    })
                
                captain_mark = " (C)" if is_captain else " (V)" if is_vice else ""
                
                if rank:
                    all_ranks.append(rank)
                    if rank <= 3:
                        rank_class = "rank-gold"
                    elif rank <= 10:
                        rank_class = "rank-silver"
                    elif rank <= 25:
                        rank_class = "rank-bronze"
                    else:
                        rank_class = "rank-normal"
                    rank_text = f"#{rank}"
                    score_text = f"{score:.2f}"
                else:
                    rank_class = "rank-na"
                    rank_text = "-"
                    score_text = "-"
                
                # Posisjons-badge farger (inline for e-post-kompatibilitet)
                pos_colors = {
                    'gkp': ('background-color: #ffcc00; color: #000;', '#ffcc00'),
                    'def': ('background-color: #00ff87; color: #000;', '#00ff87'),
                    'mid': ('background-color: #00bfff; color: #000;', '#00bfff'),
                    'fwd': ('background-color: #ff6b6b; color: #fff;', '#ff6b6b')
                }
                pos_style = pos_colors.get(pos_type.lower(), ('background-color: #ccc; color: #000;', '#ccc'))[0]
                
                # Rank farger
                rank_colors = {
                    'rank-gold': 'color: #ffd700; font-weight: bold;',
                    'rank-silver': 'color: #c0c0c0; font-weight: bold;',
                    'rank-bronze': 'color: #cd7f32; font-weight: bold;',
                    'rank-normal': 'color: #8b949e;',
                    'rank-na': 'color: #484f58;'
                }
                rank_style = rank_colors.get(rank_class, 'color: #888;')
                
                row_html = f'''
                <tr>
                    <td style="padding: 10px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117;"><span style="{pos_style} padding: 3px 8px; border-radius: 5px; font-size: 0.8em; font-weight: bold;">{pos_type}</span></td>
                    <td style="padding: 10px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117; font-weight: 600; color: #e6edf3;">{name}{captain_mark}</td>
                    <td style="padding: 10px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117;"><span style="background-color: #30363d; color: #8b949e; padding: 2px 6px; border-radius: 4px; font-size: 0.85em;">{team}</span></td>
                    <td style="padding: 10px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117; color: #00ff87; font-weight: 600;">£{price:.1f}m</td>
                    <td style="padding: 10px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117;"><span style="{rank_style}">{rank_text}</span></td>
                    <td style="padding: 10px 8px; border-bottom: 1px solid #30363d; background-color: #0d1117; font-weight: bold; color: #00ff87;">{score_text}</td>
                </tr>'''
                
                if position <= 11:
                    startere_html += row_html
                else:
                    benk_html += row_html
            
            # Beregn statistikk
            avg_rank = sum(all_ranks) / len(all_ranks) if all_ranks else 0
            top_10 = sum(1 for r in all_ranks if r <= 10)
            top_25 = sum(1 for r in all_ranks if r <= 25)
            
            # === ANBEFALT KAPTEIN ===
            # Finn spilleren med høyest xPts blant dine startere
            best_captain = None
            if my_players:
                best_captain = max(my_players, key=lambda x: x['score'])
            
            captain_html = ""
            if best_captain:
                captain_html = f'''
                    <div style="background-color: rgba(255,215,0,0.1); border: 1px solid rgba(255,215,0,0.3); border-radius: 12px; padding: 15px; margin-bottom: 15px;">
                        <div style="font-size: 1.1em; font-weight: bold; color: #ffd700; margin-bottom: 10px;">👑 Anbefalt Kaptein</div>
                        <table width="100%" cellpadding="0" cellspacing="0">
                            <tr>
                                <td style="vertical-align: middle;">
                                    <span style="font-size: 1.4em; font-weight: bold; color: #e6edf3;">{best_captain['name']}</span>
                                    <span style="background-color: #30363d; color: #8b949e; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; margin-left: 10px;">{best_captain['team']}</span>
                                </td>
                                <td align="right" style="vertical-align: middle;">
                                    <span style="background-color: #00ff87; color: #0d1117; padding: 8px 15px; border-radius: 8px; font-weight: bold;">xPts: {best_captain['score']:.2f}</span>
                                </td>
                            </tr>
                        </table>
                    </div>'''
            
            # === TRANSFER-ANBEFALINGER ===
            # Finn spillere med lav rangering og foreslå erstatninger
            transfer_recommendations = []
            
            # Sorter dine spillere etter dårligst rangering
            worst_players = sorted([p for p in my_players if p['rank'] > 15], key=lambda x: -x['rank'])[:3]
            
            for worst in worst_players:
                pos = worst['pos_type']
                max_price = worst['price'] + 0.5  # Kan bruke litt mer
                
                # Finn beste erstatning basert på posisjon
                if pos == 'FWD':
                    candidates = spisser_df[
                        (~spisser_df['id'].isin(my_player_ids)) & 
                        (spisser_df['pris_mill'] <= max_price)
                    ].head(1)
                    score_col = 'total_vektet_spiss_vurdering'
                elif pos == 'MID':
                    candidates = midtbane_df[
                        (~midtbane_df['id'].isin(my_player_ids)) & 
                        (midtbane_df['pris_mill'] <= max_price)
                    ].head(1)
                    score_col = 'total_vektet_midtbane_vurdering'
                elif pos == 'DEF':
                    candidates = forsvar_df[
                        (~forsvar_df['id'].isin(my_player_ids)) & 
                        (forsvar_df['pris_mill'] <= max_price)
                    ].head(1)
                    score_col = 'xPts_adjusted'
                elif pos == 'GKP':
                    candidates = keeper_df[
                        (~keeper_df['id'].isin(my_player_ids)) & 
                        (keeper_df['pris_mill'] <= max_price)
                    ].head(1)
                    score_col = 'xPts_adjusted'
                else:
                    continue
                
                if not candidates.empty:
                    replacement = candidates.iloc[0]
                    replacement_team = self.teams_df[self.teams_df['id'] == replacement['team']].iloc[0]['short_name']
                    transfer_recommendations.append({
                        'out_name': worst['name'],
                        'out_team': worst['team'],
                        'out_rank': worst['rank'],
                        'out_price': worst['price'],
                        'in_name': replacement['web_name'],
                        'in_team': replacement_team,
                        'in_rank': 1,  # Top i sin posisjon blant tilgjengelige
                        'in_price': replacement['pris_mill'],
                        'in_score': replacement[score_col]
                    })
            
            # Bygg transfer HTML
            transfers_html = ""
            if transfer_recommendations:
                transfer_rows = ""
                for i, tr in enumerate(transfer_recommendations[:3], 1):
                    transfer_rows += f'''
                        <tr>
                            <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; vertical-align: middle; background-color: #0d1117;">
                                <span style="background-color: #ff6b6b; color: white; padding: 3px 8px; border-radius: 50%; font-weight: bold; font-size: 0.8em;">{i}</span>
                            </td>
                            <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; vertical-align: middle; background-color: #0d1117;">
                                <div style="color: #ff6b6b; font-weight: 600;">{tr['out_name']}</div>
                                <div style="font-size: 0.8em; color: #8b949e;">#{tr['out_rank']} • £{tr['out_price']:.1f}m</div>
                            </td>
                            <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; text-align: center; vertical-align: middle; background-color: #0d1117;">
                                <span style="font-size: 1.5em;">➡️</span>
                            </td>
                            <td style="padding: 12px 8px; border-bottom: 1px solid #30363d; vertical-align: middle; background-color: #0d1117;">
                                <div style="color: #00ff87; font-weight: 600;">{tr['in_name']}</div>
                                <div style="font-size: 0.8em; color: #8b949e;">#{tr['in_rank']} • £{tr['in_price']:.1f}m • xPts: {tr['in_score']:.2f}</div>
                            </td>
                        </tr>'''
                
                transfers_html = f'''
                    <div style="background-color: #161b22; border-radius: 12px; padding: 15px; margin-bottom: 15px; border: 1px solid #30363d;">
                        <div style="font-size: 1.1em; font-weight: bold; color: #e6edf3; margin-bottom: 12px;">🔄 Topp 3 Anbefalte Transfers</div>
                        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse;">
                            <thead>
                                <tr>
                                    <th style="padding: 8px; text-align: left; color: #8b949e; font-size: 0.8em; background-color: #0d1117;">#</th>
                                    <th style="padding: 8px; text-align: left; color: #8b949e; font-size: 0.8em; background-color: #0d1117;">UT</th>
                                    <th style="padding: 8px; text-align: center; color: #8b949e; font-size: 0.8em; background-color: #0d1117;"></th>
                                    <th style="padding: 8px; text-align: left; color: #8b949e; font-size: 0.8em; background-color: #0d1117;">INN</th>
                                </tr>
                            </thead>
                            <tbody>
                                {transfer_rows}
                            </tbody>
                        </table>
                    </div>'''
            
            html = f'''
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #161b22; border-radius: 16px; margin-bottom: 24px; border: 1px solid #30363d;">
            <tr>
                <td style="padding: 25px;">
                    <!-- Header med lag-navn og stats -->
                    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px; border-bottom: 1px solid #30363d; padding-bottom: 20px;">
                        <tr>
                            <td style="vertical-align: top;">
                                <div style="font-size: 1.8em; font-weight: bold; color: #00ff87; margin-bottom: 5px;">⚽ {team_name}</div>
                                <div style="font-size: 0.95em; color: #8b949e;">Rangert mot vår AI-analyse • Gameweek {current_gw}</div>
                            </td>
                            <td align="right" style="vertical-align: top;">
                                <table cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td style="background-color: #0d1117; border: 1px solid #00ff87; padding: 15px 25px; border-radius: 12px; text-align: center; margin-right: 10px;">
                                            <div style="font-size: 1.8em; font-weight: bold; color: #00ff87;">#{avg_rank:.0f}</div>
                                            <div style="font-size: 0.75em; color: #8b949e; text-transform: uppercase;">Snittrangering</div>
                                        </td>
                                        <td width="15"></td>
                                        <td style="background-color: #0d1117; border: 1px solid #30363d; padding: 15px 25px; border-radius: 12px; text-align: center;">
                                            <div style="font-size: 1.8em; font-weight: bold; color: #e6edf3;">{top_10}</div>
                                            <div style="font-size: 0.75em; color: #8b949e; text-transform: uppercase;">Topp 10</div>
                                        </td>
                                        <td width="15"></td>
                                        <td style="background-color: #0d1117; border: 1px solid #30363d; padding: 15px 25px; border-radius: 12px; text-align: center;">
                                            <div style="font-size: 1.8em; font-weight: bold; color: #e6edf3;">{top_25}</div>
                                            <div style="font-size: 0.75em; color: #8b949e; text-transform: uppercase;">Topp 25</div>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>
                    </table>
                    
                    {captain_html}
                    
                    {transfers_html}
                    
                    <!-- Startoppstilling -->
                    <div style="background-color: #0d1117; border-radius: 12px; padding: 15px; margin-bottom: 15px; border: 1px solid #30363d;">
                        <div style="font-size: 1.1em; font-weight: bold; color: #e6edf3; margin-bottom: 12px;">⚽ Startoppstilling</div>
                        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse;">
                            <thead>
                                <tr>
                                    <th style="background-color: #30363d; color: #e6edf3; padding: 10px 8px; text-align: left; font-weight: 600;">Pos</th>
                                    <th style="background-color: #30363d; color: #e6edf3; padding: 10px 8px; text-align: left; font-weight: 600;">Spiller</th>
                                    <th style="background-color: #30363d; color: #e6edf3; padding: 10px 8px; text-align: left; font-weight: 600;">Lag</th>
                                    <th style="background-color: #30363d; color: #e6edf3; padding: 10px 8px; text-align: left; font-weight: 600;">Pris</th>
                                    <th style="background-color: #30363d; color: #e6edf3; padding: 10px 8px; text-align: left; font-weight: 600;">Rank</th>
                                    <th style="background-color: #30363d; color: #e6edf3; padding: 10px 8px; text-align: left; font-weight: 600;">xPts</th>
                                </tr>
                            </thead>
                            <tbody>
                                {startere_html}
                            </tbody>
                        </table>
                    </div>
                    
                    <!-- Benk -->
                    <div style="background-color: #0d1117; border-radius: 12px; padding: 15px; border: 1px solid #30363d;">
                        <div style="font-size: 1.1em; font-weight: bold; color: #8b949e; margin-bottom: 12px;">🪑 Benk</div>
                        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse;">
                            <thead>
                                <tr>
                                    <th style="background-color: #21262d; color: #8b949e; padding: 10px 8px; text-align: left; font-weight: 600;">Pos</th>
                                    <th style="background-color: #21262d; color: #8b949e; padding: 10px 8px; text-align: left; font-weight: 600;">Spiller</th>
                                    <th style="background-color: #21262d; color: #8b949e; padding: 10px 8px; text-align: left; font-weight: 600;">Lag</th>
                                    <th style="background-color: #21262d; color: #8b949e; padding: 10px 8px; text-align: left; font-weight: 600;">Pris</th>
                                    <th style="background-color: #21262d; color: #8b949e; padding: 10px 8px; text-align: left; font-weight: 600;">Rank</th>
                                    <th style="background-color: #21262d; color: #8b949e; padding: 10px 8px; text-align: left; font-weight: 600;">xPts</th>
                                </tr>
                            </thead>
                            <tbody>
                                {benk_html}
                            </tbody>
                        </table>
                    </div>
                </td>
            </tr>
        </table>
        '''
            
            return html
            
        except Exception as e:
            print(f"Feil ved henting av mitt lag: {e}")
            return ""
    
    def _df_to_html_table(self, df, position_type):
        """Konverterer en DataFrame til en stylet HTML-tabell"""
        if df is None or df.empty:
            return "<p>No data available</p>"
        
        html = "<table><thead><tr><th>#</th>"
        for col in df.columns:
            html += f"<th>{col}</th>"
        html += "</tr></thead><tbody>"
        
        for idx, (_, row) in enumerate(df.iterrows(), 1):
            medal = ""
            if idx == 1:
                medal = '<span class="medal gold">1</span>'
            elif idx == 2:
                medal = '<span class="medal silver">2</span>'
            elif idx == 3:
                medal = '<span class="medal bronze">3</span>'
            else:
                medal = f'<span class="rank">{idx}</span>'
            
            html += f"<tr><td>{medal}</td>"
            for col_idx, val in enumerate(row):
                col_name = df.columns[col_idx]
                
                # Style basert på kolonne
                if col_name == 'name':
                    html += f'<td class="player-name">{val}</td>'
                elif col_name == 'lag':
                    html += f'<td><span class="team-badge">{val}</span></td>'
                elif col_name == 'pris':
                    html += f'<td class="price">£{val}m</td>'
                elif col_name in ['total', 'xPts_ad', 'xPts_base']:
                    html += f'<td><span class="score">{val}</span></td>'
                else:
                    html += f'<td>{val}</td>'
            html += "</tr>"
        
        html += "</tbody></table>"
        return html
    
    def generer_rapport_for_abonnent(self, team_id, name, output_dir="reports"):
        """Genererer en personlig rapport for en abonnent"""
        import os
        
        # Opprett output-mappe hvis den ikke finnes
        os.makedirs(output_dir, exist_ok=True)
        
        # Generer filnavn basert på team_id
        filnavn = f"{output_dir}/FPL_Report_{team_id}.html"
        
        # Oppdater team_id for mitt lag-seksjonen
        self._current_subscriber_team_id = team_id
        self._current_subscriber_name = name
        
        # Generer HTML-rapport med personlig lag
        self._generer_personlig_html_rapport(filnavn, team_id, name)
        
        return filnavn
    
    def _generer_personlig_html_rapport(self, filnavn, team_id, subscriber_name):
        """Genererer en personlig HTML-rapport for en abonnent"""
        
        # Hent data
        keepere = self.beste_keepere(antall=15, min_minutter=180)
        spisser = self.beste_spisser_avansert(antall=15, min_minutter=180)
        midtbane = self.beste_midtbanespillere(antall=15, min_minutter=180)
        forsvar = self.beste_forsvarsspillere(antall=15, min_minutter=180)
        
        # Hent personlig lag data
        mitt_lag_html = self._get_mitt_lag_html(team_id=team_id)
        
        # Hent value for money HTML
        value_for_money_html = self._get_value_for_money_html()
        
        # Hent drømmelag HTML
        drommelag_html = self._get_drommelag_html()
        
        # Hent kombinert greeting og deadline
        combined_greeting_html = self._get_combined_greeting_deadline_html(subscriber_name)
        
        html = f'''<!DOCTYPE html>
<html lang="no">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FPL Analyse - {subscriber_name}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary: #00ff87;
            --primary-dark: #00d972;
            --dark: #0d1117;
            --darker: #010409;
            --card-bg: #161b22;
            --border: #30363d;
            --text: #e6edf3;
            --text-muted: #8b949e;
        }}
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Outfit', -apple-system, sans-serif;
            background: var(--darker);
            min-height: 100vh;
            padding: 20px;
            color: var(--text);
            line-height: 1.6;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            padding: 40px 20px;
            background: var(--card-bg);
            border-radius: 16px;
            margin-bottom: 24px;
            border: 1px solid var(--border);
        }}
        .header h1 {{
            font-size: 2.2em;
            margin-bottom: 8px;
            color: var(--text);
            font-weight: 800;
        }}
        .header h1 span {{
            color: var(--primary);
        }}
        .header .subtitle {{
            font-size: 1.1em;
            color: var(--text-muted);
        }}
            text-shadow: none;
        }}
        .header .subtitle {{
            font-size: 1.2em;
            color: #2c5282;
        }}
        .combined-greeting-deadline {{
            background: var(--card-bg);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid var(--border);
        }}
        .greeting-row {{
            margin-bottom: 16px;
        }}
        .greeting-text {{
            font-size: 1.4em;
            font-weight: 700;
            color: var(--primary);
        }}
        .deadline-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
        }}
        .deadline-info {{
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        .deadline-icon {{
            font-size: 2.5em;
        }}
        .deadline-details {{
            flex: 1;
        }}
        .deadline-title {{
            font-size: 1em;
            font-weight: 600;
            color: var(--text-muted);
        }}
        .deadline-time {{
            font-size: 1.8em;
            font-weight: 800;
            color: var(--primary);
        }}
        .deadline-date {{
            font-size: 0.9em;
            color: var(--text-muted);
        }}
        .deadline-warning {{
            background: rgba(255, 107, 107, 0.2);
            color: #ff6b6b;
            padding: 10px 20px;
            border-radius: 10px;
            font-weight: 600;
            border: 1px solid rgba(255, 107, 107, 0.3);
        }}
        .personal-greeting {{
            background: var(--card-bg);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
            text-align: center;
            font-size: 1.3em;
            border: 1px solid var(--border);
        }}
        .deadline-box {{
            background: var(--card-bg);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 24px;
            text-align: center;
            border: 1px solid var(--primary);
        }}
        .section {{
            background: var(--card-bg);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid var(--border);
        }}
        .section-header {{
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }}
        .section-icon {{
            font-size: 2em;
        }}
        .section-title {{
            font-size: 1.4em;
            font-weight: 700;
            color: var(--text);
        }}
        .section-desc {{
            font-size: 0.85em;
            color: var(--text-muted);
            margin-top: 4px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--dark);
            border-radius: 12px;
            overflow: hidden;
        }}
        th {{
            background: var(--border);
            color: var(--text);
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        td {{
            padding: 10px 8px;
            border-bottom: 1px solid var(--border);
            font-size: 0.9em;
            color: var(--text);
            background: var(--dark);
        }}
        tr:hover td {{
            background: rgba(0, 255, 135, 0.05);
        }}
        tr:nth-child(1) td {{ background: rgba(255, 215, 0, 0.15); }}
        tr:nth-child(2) td {{ background: rgba(192, 192, 192, 0.1); }}
        tr:nth-child(3) td {{ background: rgba(205, 127, 50, 0.1); }}
        .medal {{
            display: inline-block;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            text-align: center;
            line-height: 28px;
            font-weight: bold;
            font-size: 0.9em;
        }}
        .gold {{ background: linear-gradient(135deg, #ffd700 0%, #ffb800 100%); color: var(--dark); }}
        .silver {{ background: linear-gradient(135deg, #c0c0c0 0%, #a8a8a8 100%); color: var(--dark); }}
        .bronze {{ background: linear-gradient(135deg, #cd7f32 0%, #b87333 100%); color: var(--dark); }}
        .rank {{ color: var(--text-muted); font-weight: bold; }}
        .pos-badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 5px;
            font-size: 0.8em;
            font-weight: bold;
        }}
        .pos-gkp {{ background: #ebff00; color: var(--dark); }}
        .pos-def {{ background: var(--primary); color: var(--dark); }}
        .pos-mid {{ background: #05f0ff; color: var(--dark); }}
        .pos-fwd {{ background: #e90052; color: #ffffff; }}
        .player-name {{
            font-weight: 600;
            color: var(--text);
        }}
        .team-badge {{
            background: var(--border);
            color: var(--text-muted);
            padding: 3px 8px;
            border-radius: 5px;
            font-size: 0.85em;
        }}
        .price {{ color: var(--primary); font-weight: 600; }}
        .score {{
            background: var(--primary);
            color: var(--dark);
            padding: 5px 10px;
            border-radius: 8px;
            font-weight: bold;
        }}
        .footer {{
            text-align: center;
            padding: 30px;
            color: var(--text-muted);
            font-size: 0.9em;
        }}
        .footer a {{
            color: var(--text-muted);
            text-decoration: none;
        }}
        .footer a:hover {{
            color: var(--primary);
        }}
        .dream-team-section {{
            background: linear-gradient(135deg, rgba(255, 215, 0, 0.1) 0%, rgba(255, 140, 0, 0.1) 100%);
            border: 1px solid rgba(255, 215, 0, 0.3);
        }}
        .dream-team-section .section-title {{ color: #ffd700; }}
        .dream-team-section .section-desc {{ color: var(--text-muted); }}
        .dream-team-section table {{ background: var(--dark); }}
        .dream-team-section th {{ background: rgba(255, 215, 0, 0.2) !important; color: #ffd700 !important; }}
        .dream-team-section td {{ color: var(--text); }}
        .xpts-badge {{
            background: var(--primary);
            color: var(--dark);
            padding: 3px 8px;
            border-radius: 5px;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚽ FPL <span>Analyse</span></h1>
            <div class="subtitle">AI-drevet innsikt og anbefalinger for ditt lag</div>
        </div>
        
        {combined_greeting_html}
        
        {mitt_lag_html}
        
        {value_for_money_html}
        
        {drommelag_html}
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🧤</span>
                <div>
                    <div class="section-title">Top 15 Goalkeepers - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 4×CS + 0.33×Saves + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(keepere, 'GKP')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🛡️</span>
                <div>
                    <div class="section-title">Top 15 Defenders - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 4×CS + 6×xG + 3×xA + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(forsvar, 'DEF')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🎯</span>
                <div>
                    <div class="section-title">Top 15 Midfielders - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 5×xG + 3×xA + 1×CS + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(midtbane, 'MID')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">⭐</span>
                <div>
                    <div class="section-title">Top 15 Forwards - Expected Points (xPts)</div>
                    <div class="section-desc">xPts Model: 4×xG + 3×xA + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(spisser, 'FWD')}
        </div>
        
        <div class="footer">
            <p>FPL Analyse • AI-drevet innsikt for Fantasy Premier League</p>
            <p>Rapport generert: {datetime.now().strftime('%d.%m.%Y kl. %H:%M')}</p>
            <p style="margin-top: 15px;"><a href="https://fplanalyse.no/unsubscribe.html">Meld deg av tjenesten</a></p>
        </div>
    </div>
</body>
</html>'''
        
        # Skriv til fil
        with open(filnavn, 'w', encoding='utf-8') as f:
            f.write(html)
        
        print(f"✓ Personlig rapport generert for {subscriber_name}: {filnavn}")
        return filnavn
    
    def generer_alle_abonnent_rapporter(self, subscribers_file="subscribers.json"):
        """Genererer rapporter for alle abonnenter fra JSON-fil"""
        import json
        import os
        
        try:
            print(f"\n📂 Ser etter abonnentfil: {subscribers_file}")
            print(f"📂 Nåværende mappe: {os.getcwd()}")
            print(f"📂 Filer i mappen: {os.listdir('.')}")
            
            with open(subscribers_file, 'r', encoding='utf-8') as f:
                subscribers = json.load(f)
            
            print(f"\n📧 Genererer rapporter for {len(subscribers)} abonnenter...")
            
            generated_reports = []
            for sub in subscribers:
                name = sub.get('name', 'Unknown')
                email = sub.get('email', '')
                team_id = sub.get('team_id', 0)
                
                print(f"  Behandler: {name} (team_id={team_id}, email={email})")
                
                if team_id and email:
                    try:
                        filnavn = self.generer_rapport_for_abonnent(team_id, name)
                        generated_reports.append({
                            'name': name,
                            'email': email,
                            'team_id': team_id,
                            'report_file': filnavn
                        })
                    except Exception as e:
                        print(f"  ⚠️ Feil ved generering for {name}: {e}")
                else:
                    print(f"  ⚠️ Mangler team_id eller email for {name}")
            
            # Lagre liste over genererte rapporter
            print(f"\n💾 Lagrer generated_reports.json med {len(generated_reports)} rapporter...")
            with open('generated_reports.json', 'w', encoding='utf-8') as f:
                json.dump(generated_reports, f, indent=2)
            
            print(f"✓ {len(generated_reports)} rapporter generert")
            print(f"📂 Filer etter generering: {os.listdir('.')}")
            
            return generated_reports
            
        except FileNotFoundError as e:
            print(f"⚠️ Finner ikke {subscribers_file}: {e}")
            print(f"📂 Filer i mappen: {os.listdir('.')}")
            return []
        except json.JSONDecodeError as e:
            print(f"⚠️ Feil i JSON-format: {e}")
            return []
        except Exception as e:
            print(f"⚠️ Uventet feil: {e}")
            import traceback
            traceback.print_exc()
            return []


# Hovedprogram
if __name__ == "__main__":
    import sys
    import json
    
    print("Starter FPL Analysator (Avansert versjon)...")
    print("-"*100)
    
    # Opprett analyzer
    analyzer = FPLAnalyzer()
    
    # Hent data
    if analyzer.hent_data():
        analyzer.hent_fixtures()
        analyzer.lag_spillerdataframe()
        
        # Sjekk om vi skal generere for abonnenter
        if len(sys.argv) > 1 and sys.argv[1] == '--subscribers':
            # Generer rapporter for alle abonnenter
            subscribers_file = sys.argv[2] if len(sys.argv) > 2 else 'subscribers.json'
            analyzer.generer_alle_abonnent_rapporter(subscribers_file)
        else:
            # Standard: Generer én rapport
            analyzer.generer_html_rapport("Fantasy_Premier_League_recommendations.html")
            analyzer.vis_rapport()
    else:
        print("Kunne ikke hente data fra FPL API")
