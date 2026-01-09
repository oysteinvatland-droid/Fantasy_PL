import requests
import pandas as pd
from datetime import datetime
import numpy as np
import warnings
import urllib3

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
                # Hjemmekamp - se på bortelagets forsvarsstyrke
                difficulty = kamp['team_a_difficulty']
            else:
                # Bortekamp - se på hjemmelagets forsvarsstyrke
                difficulty = kamp['team_h_difficulty']
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
        Avansert spiss-vurdering med flere parametre:
        - xG per 90 min (justert for spilletid)
        - Form trend (ikke bare nåværende)
        - Fixture difficulty neste 5 kamper
        - Team attack strength
        - Underlying stats (shots, big chances)
        - Bonus potential
        - Pris-verdi
        """
        if vekter is None:
            vekter = {
                'xg_per_90': 0.22,        # Expected goals per 90 min
                'form': 0.15,              # Nåværende form
                'fixture_ease': 0.18,      # Letthet i kommende kamper (invertert difficulty)
                'team_attack': 0.12,       # Lagets angrepssstyrke
                'threat': 0.10,            # Angrepsfare (ICT)
                'bonus_potential': 0.08,   # Bonus poeng per kamp
                'ppm': 0.10,               # Verdi for pengene
                'minutes_reliability': 0.05 # Spilletid-stabilitet
            }
        
        df = self.beregn_metrics()
        if df is None:
            return None
        
        # Filtrer kun spisser
        df = df[df['posisjon'] == 'FWD'].copy()
        
        # 1. xG per 90 minutter
        df['xg_per_90'] = df.apply(
            lambda x: (x['expected_goals'] / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        
        # 2. Fixture difficulty (hent for hvert lag)
        if self.fixtures is not None:
            team_fixture_difficulty = {}
            for team_id in df['team'].unique():
                fdr = self.beregn_fixture_difficulty(team_id, 5)
                team_fixture_difficulty[team_id] = fdr if fdr else 3  # Default til middels
            
            df['fixture_difficulty'] = df['team'].map(team_fixture_difficulty)
            # Inverter slik at lettere kamper = høyere score (5 - difficulty for å flippe skalaen)
            df['fixture_ease'] = 6 - df['fixture_difficulty']
        else:
            df['fixture_ease'] = 3  # Neutral hvis ikke tilgjengelig
        
        # 3. Team attack strength
        team_attack = self.beregn_team_attack_strength()
        df['team_attack_strength'] = df['team'].apply(
            lambda x: team_attack.get(x, {}).get('combined_attack', 50)
        )
        
        # 4. Bonus potential (bonus per kamp spilt)
        df['kamper_spilt'] = (df['minutes'] / 90).round()
        df['bonus_per_kamp'] = df.apply(
            lambda x: x['bonus'] / x['kamper_spilt'] if x['kamper_spilt'] > 0 else 0,
            axis=1
        )
        
        # 5. Minutes reliability (hvor konsistent er spilletiden)
        # Bruk starts som proxy for spilletid-stabilitet
        df['starts'] = pd.to_numeric(df['starts'], errors='coerce').fillna(0)
        df['minutes_reliability'] = df.apply(
            lambda x: float(x['starts'] / x['kamper_spilt']) if x['kamper_spilt'] > 0 else 0,
            axis=1
        )
        
        # Normaliser alle metrics til 0-100 skala
        def normaliser(serie):
            min_val = serie.min()
            max_val = serie.max()
            if max_val - min_val == 0:
                return pd.Series([50] * len(serie), index=serie.index)
            return ((serie - min_val) / (max_val - min_val) * 100)
        
        df['xg_per_90_norm'] = normaliser(df['xg_per_90'])
        df['form_norm'] = normaliser(df['form_num'])
        df['fixture_ease_norm'] = normaliser(df['fixture_ease'])
        df['team_attack_norm'] = normaliser(df['team_attack_strength'])
        df['threat_norm'] = normaliser(df['threat'])
        df['bonus_potential_norm'] = normaliser(df['bonus_per_kamp'])
        df['ppm_norm'] = normaliser(df['ppm'])
        df['minutes_reliability_norm'] = normaliser(df['minutes_reliability'])
        
        # Beregn total vektet score
        df['total_vektet_spiss_vurdering'] = (
            df['xg_per_90_norm'] * vekter['xg_per_90'] +
            df['form_norm'] * vekter['form'] +
            df['fixture_ease_norm'] * vekter['fixture_ease'] +
            df['team_attack_norm'] * vekter['team_attack'] +
            df['threat_norm'] * vekter['threat'] +
            df['bonus_potential_norm'] * vekter['bonus_potential'] +
            df['ppm_norm'] * vekter['ppm'] +
            df['minutes_reliability_norm'] * vekter['minutes_reliability']
        ) * 100
        
        return df
    
    def beste_spisser_avansert(self, antall=15, min_minutter=180, maks_pris=None):
        """Finner de beste spissene basert på avansert sammensatt score"""
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
            'web_name', 'lag_short', 'pris_mill', 'total_vektet_spiss_vurdering',
            'xg_per_90', 'form_num', 'fixture_difficulty', 'team_attack_strength',
            'ppm', 'bonus_per_kamp', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter etter total_vektet_spiss_vurdering
        resultat = df[kolonner].sort_values(by='total_vektet_spiss_vurdering', ascending=False).head(antall)
        
        # Rund av for bedre lesbarhet
        resultat['total_vektet_spiss_vurdering'] = resultat['total_vektet_spiss_vurdering'].round(1)
        resultat['xg_per_90'] = resultat['xg_per_90'].round(2)
        resultat['ppm'] = resultat['ppm'].round(2)
        resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
        resultat['bonus_per_kamp'] = resultat['bonus_per_kamp'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        # Gi kolonnene kortere, mer lesbare navn
        resultat = resultat.rename(columns={
            'web_name': 'name',
            'lag_short': 'lag',
            'pris_mill': 'pris',
            'total_vektet_spiss_vurdering': 'total',
            'fixture_difficulty': 'fix_diff',
            'team_attack_strength': 'team_str'
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
        Avansert midtbanespiller-vurdering med spesifikke parametre:
        - Expected Goal Involvements (xG + xA) per 90
        - Creativity (ICT komponenten)
        - Set piece taker potential
        - Team attack strength
        - Bonus potential
        - Fixture difficulty
        - Pris-verdi
        - Minutes reliability
        """
        if vekter is None:
            vekter = {
                'xgi_per_90': 0.25,        # Expected goal involvements per 90 min (scorer + assister)
                'creativity': 0.18,         # Creativity index - nøkkelpasser, sjanser skapt
                'form': 0.15,               # Nåværende form
                'fixture_ease': 0.12,       # Letthet i kommende kamper
                'team_attack': 0.10,        # Lagets angrepssstyrke
                'bonus_potential': 0.10,    # Bonus poeng per kamp (viktigere for midtbane)
                'ppm': 0.08,                # Verdi for pengene
                'minutes_reliability': 0.02 # Spilletid-stabilitet (mindre rotasjon)
            }
        
        df = self.beregn_metrics()
        if df is None:
            return None
        
        # Filtrer kun midtbanespillere
        df = df[df['posisjon'] == 'MID'].copy()
        
        # 1. Expected Goal Involvements per 90 minutter (xG + xA)
        df['xgi'] = df['expected_goals'] + df['expected_assists']
        df['xgi_per_90'] = df.apply(
            lambda x: (x['xgi'] / x['minutes'] * 90) if x['minutes'] > 0 else 0,
            axis=1
        )
        
        # 2. Creativity score er allerede i dataen
        df['creativity_num'] = pd.to_numeric(df['creativity'], errors='coerce').fillna(0)
        
        # 3. Fixture difficulty (hent for hvert lag)
        if self.fixtures is not None:
            team_fixture_difficulty = {}
            for team_id in df['team'].unique():
                fdr = self.beregn_fixture_difficulty(team_id, 5)
                team_fixture_difficulty[team_id] = fdr if fdr else 3
            
            df['fixture_difficulty'] = df['team'].map(team_fixture_difficulty)
            df['fixture_ease'] = 6 - df['fixture_difficulty']
        else:
            df['fixture_ease'] = 3
        
        # 4. Team attack strength
        team_attack = self.beregn_team_attack_strength()
        df['team_attack_strength'] = df['team'].apply(
            lambda x: team_attack.get(x, {}).get('combined_attack', 50)
        )
        
        # 5. Bonus potential (bonus per kamp spilt)
        df['kamper_spilt'] = (df['minutes'] / 90).round()
        df['bonus_per_kamp'] = df.apply(
            lambda x: x['bonus'] / x['kamper_spilt'] if x['kamper_spilt'] > 0 else 0,
            axis=1
        )
        
        # 6. Minutes reliability
        df['starts'] = pd.to_numeric(df['starts'], errors='coerce').fillna(0)
        df['minutes_reliability'] = df.apply(
            lambda x: float(x['starts'] / x['kamper_spilt']) if x['kamper_spilt'] > 0 else 0,
            axis=1
        )
        
        # Normaliser alle metrics til 0-100 skala
        def normaliser(serie):
            min_val = serie.min()
            max_val = serie.max()
            if max_val - min_val == 0:
                return pd.Series([50] * len(serie), index=serie.index)
            return ((serie - min_val) / (max_val - min_val) * 100)
        
        df['xgi_per_90_norm'] = normaliser(df['xgi_per_90'])
        df['creativity_norm'] = normaliser(df['creativity_num'])
        df['form_norm'] = normaliser(df['form_num'])
        df['fixture_ease_norm'] = normaliser(df['fixture_ease'])
        df['team_attack_norm'] = normaliser(df['team_attack_strength'])
        df['bonus_potential_norm'] = normaliser(df['bonus_per_kamp'])
        df['ppm_norm'] = normaliser(df['ppm'])
        df['minutes_reliability_norm'] = normaliser(df['minutes_reliability'])
        
        # Beregn total vektet score
        df['total_vektet_midtbane_vurdering'] = (
            df['xgi_per_90_norm'] * vekter['xgi_per_90'] +
            df['creativity_norm'] * vekter['creativity'] +
            df['form_norm'] * vekter['form'] +
            df['fixture_ease_norm'] * vekter['fixture_ease'] +
            df['team_attack_norm'] * vekter['team_attack'] +
            df['bonus_potential_norm'] * vekter['bonus_potential'] +
            df['ppm_norm'] * vekter['ppm'] +
            df['minutes_reliability_norm'] * vekter['minutes_reliability']
        ) * 100
        
        return df
    
    def beste_midtbanespillere(self, antall=15, min_minutter=180, maks_pris=None):
        """Finner de beste midtbanespillerne basert på avansert sammensatt score"""
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
            'web_name', 'lag_short', 'pris_mill', 'total_vektet_midtbane_vurdering',
            'xgi_per_90', 'creativity_num', 'form_num', 'fixture_difficulty',
            'ppm', 'bonus_per_kamp', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter etter total_vektet_midtbane_vurdering
        resultat = df[kolonner].sort_values(by='total_vektet_midtbane_vurdering', ascending=False).head(antall)
        
        # Rund av for bedre lesbarhet
        resultat['total_vektet_midtbane_vurdering'] = resultat['total_vektet_midtbane_vurdering'].round(1)
        resultat['xgi_per_90'] = resultat['xgi_per_90'].round(2)
        resultat['creativity_num'] = resultat['creativity_num'].round(1)
        resultat['ppm'] = resultat['ppm'].round(2)
        resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
        resultat['bonus_per_kamp'] = resultat['bonus_per_kamp'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        # Gi kolonnene kortere, mer lesbare navn
        resultat = resultat.rename(columns={
            'web_name': 'name',
            'lag_short': 'lag',
            'pris_mill': 'pris',
            'total_vektet_midtbane_vurdering': 'total',
            'fixture_difficulty': 'fix_diff',
            'creativity_num': 'creativity'
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
        # Hent spillerhistorikk for å se på recent form
        print("Henter spilletidsdata for siste 4 kamper...")
        
        def hent_siste_4_kamper_stats(player_id):
            """Henter spilletidsstatistikk for siste 4 kamper"""
            try:
                url = f"https://fantasy.premierleague.com/api/element-summary/{player_id}/"
                response = requests.get(url, verify=False, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    history = data.get('history', [])
                    
                    # Ta de siste 4 kampene
                    siste_4 = history[-4:] if len(history) >= 4 else history
                    
                    if len(siste_4) == 0:
                        return {'starts_siste_4': 0, 'minutter_siste_4': 0, 'kamper_siste_4': 0}
                    
                    # Tell starts (minutter >= 60 regnes som start)
                    starts = sum(1 for kamp in siste_4 if kamp.get('minutes', 0) >= 60)
                    total_minutter = sum(kamp.get('minutes', 0) for kamp in siste_4)
                    
                    return {
                        'starts_siste_4': starts,
                        'minutter_siste_4': total_minutter,
                        'kamper_siste_4': len(siste_4)
                    }
            except:
                pass
            return {'starts_siste_4': 0, 'minutter_siste_4': 0, 'kamper_siste_4': 0}
        
        # Hent data for alle forsvarsspillere (kan ta litt tid)
        siste_4_stats = {}
        for idx, row in df.iterrows():
            player_id = row['id']
            siste_4_stats[player_id] = hent_siste_4_kamper_stats(player_id)
        
        # Legg til siste 4 kamper data
        df['starts_siste_4'] = df['id'].apply(lambda x: siste_4_stats.get(x, {}).get('starts_siste_4', 0))
        df['minutter_siste_4'] = df['id'].apply(lambda x: siste_4_stats.get(x, {}).get('minutter_siste_4', 0))
        df['kamper_siste_4'] = df['id'].apply(lambda x: siste_4_stats.get(x, {}).get('kamper_siste_4', 4))
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
            df['xPts_adjusted'] = df['xPts_per_match'] * df['fixture_multiplier']
        else:
            df['fixture_difficulty'] = 3
            df['xPts_adjusted'] = df['xPts_per_match']
        
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
            'web_name', 'lag_short', 'pris_mill', 'xPts_adjusted', 'playing_time_probability',
            'xPts_base', 'CS_prob', 'xG_per_match', 'xA_per_match', 'Bonus_per_match',
            'fixture_difficulty', 'ppm', 'total_points', 'valgt_prosent'
        ]
        
        # Sorter etter xPts_adjusted (som inkluderer spilletid-justering og fixtures)
        resultat = df[kolonner].sort_values(by='xPts_adjusted', ascending=False).head(antall)
        
        # Rund av for bedre lesbarhet
        resultat['xPts_adjusted'] = resultat['xPts_adjusted'].round(2)
        resultat['xPts_base'] = resultat['xPts_base'].round(2)
        resultat['playing_time_probability'] = (resultat['playing_time_probability'] * 100).round(0)  # Vis som prosent
        resultat['CS_prob'] = (resultat['CS_prob'] * 100).round(1)  # Vis som prosent
        resultat['xG_per_match'] = resultat['xG_per_match'].round(3)
        resultat['xA_per_match'] = resultat['xA_per_match'].round(3)
        resultat['Bonus_per_match'] = resultat['Bonus_per_match'].round(2)
        resultat['fixture_difficulty'] = resultat['fixture_difficulty'].round(1)
        resultat['ppm'] = resultat['ppm'].round(2)
        resultat['valgt_prosent'] = resultat['valgt_prosent'].round(1)
        
        # Gi kolonnene kortere, mer lesbare navn
        resultat = resultat.rename(columns={
            'web_name': 'name',
            'lag_short': 'lag',
            'pris_mill': 'pris',
            'xPts_adjusted': 'xPts_ad',
            'playing_time_probability': 'play_%',
            'fixture_difficulty': 'fix_diff',
            'Bonus_per_match': 'bonus',
            'xG_per_match': 'xG_avg',
            'xA_per_match': 'xA_avg'
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
        print(f"   Fixture difficulty: {spiller['fixture_difficulty']:.1f}")
        
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
        print(f"   Fixture difficulty: {spiller['fixture_difficulty']:.1f}")
        
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
        
        print("\n⭐ TOPP 25 SPISSER - AVANSERT VURDERING")
        print("-"*100)
        print("Inkluderer: xG/90, Form, Fixture Difficulty (neste 5), Team Attack, Bonus, Verdi")
        print("-"*100)
        spisser = self.beste_spisser_avansert(antall=25, min_minutter=180)
        if spisser is not None:
            print(spisser.to_string(index=False))
        
        print("\n\n🎯 TOPP 25 MIDTBANESPILLERE - AVANSERT VURDERING")
        print("-"*100)
        print("Inkluderer: xGI/90 (mål+assist), Creativity, Form, Fixture, Team Attack, Bonus, Verdi")
        print("-"*100)
        midtbane = self.beste_midtbanespillere(antall=25, min_minutter=180)
        if midtbane is not None:
            print(midtbane.to_string(index=False))
        
        print("\n\n🛡️ TOPP 25 FORSVARSSPILLERE - AVANSERT VURDERING")
        print("-"*100)
        print("xPts modell: 4*CS + 6*xG + 3*xA + MinPts + Bonus (justert for fixtures og spilletid)")
        print("playing_time_probability = sannsynlighet for å starte kampen (reduserer score for benk-spillere)")
        print("-"*100)
        forsvar = self.beste_forsvarsspillere(antall=25, min_minutter=180)
        if forsvar is not None:
            print(forsvar.to_string(index=False))
        
        # Vis spesifikk analyse for Saliba og Gabriel
        print("\n\n🔍 ARSENAL-FORSVAR: SALIBA & GABRIEL")
        print("-"*100)
        arsenal_forsvar = self.vis_spillere(['Saliba', 'Gabriel'], posisjon='DEF')
        if arsenal_forsvar is not None:
            print(arsenal_forsvar.to_string(index=False))
        
        print("\n" + "="*100)
    
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
        spisser = self.beste_spisser_avansert(antall=25, min_minutter=180)
        midtbane = self.beste_midtbanespillere(antall=25, min_minutter=180)
        forsvar = self.beste_forsvarsspillere(antall=25, min_minutter=180)
        arsenal_forsvar = self.vis_spillere(['Saliba', 'Gabriel'], posisjon='DEF')
        
        # Hent deadline info
        deadline_html = self._get_deadline_html()
        
        html = f'''<!DOCTYPE html>
<html lang="no">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fantasy Premier League Recommendations</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            min-height: 100vh;
            padding: 20px;
            color: #ffffff;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            padding: 40px 20px;
            background: linear-gradient(135deg, #37003c 0%, #00ff87 100%);
            border-radius: 20px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(0, 255, 135, 0.3);
        }}
        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }}
        .header .subtitle {{
            font-size: 1.2em;
            opacity: 0.9;
        }}
        .deadline-box {{
            background: linear-gradient(135deg, #ff6b6b 0%, #feca57 100%);
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 30px;
            text-align: center;
            box-shadow: 0 5px 20px rgba(255, 107, 107, 0.3);
        }}
        .deadline-box h2 {{
            font-size: 1.5em;
            margin-bottom: 10px;
        }}
        .deadline-box .time {{
            font-size: 2em;
            font-weight: bold;
        }}
        .deadline-box.urgent {{
            animation: pulse 1s infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.02); }}
        }}
        .section {{
            background: rgba(255, 255, 255, 0.05);
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 25px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        .section-header {{
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid rgba(0, 255, 135, 0.3);
        }}
        .section-icon {{
            font-size: 2em;
        }}
        .section-title {{
            font-size: 1.5em;
            color: #00ff87;
        }}
        .section-desc {{
            font-size: 0.9em;
            color: rgba(255, 255, 255, 0.7);
            margin-top: 5px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9em;
        }}
        th {{
            background: #a8d4f0;
            color: #000000;
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
            position: sticky;
            top: 0;
        }}
        td {{
            padding: 10px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }}
        tr:hover {{
            background: rgba(0, 255, 135, 0.1);
        }}
        tr:nth-child(1) td {{ background: rgba(255, 215, 0, 0.2); }}
        tr:nth-child(2) td {{ background: rgba(192, 192, 192, 0.15); }}
        tr:nth-child(3) td {{ background: rgba(205, 127, 50, 0.15); }}
        .rank {{
            font-weight: bold;
            color: #00ff87;
        }}
        .player-name {{
            font-weight: 600;
            color: #000000;
            background: #ffffff;
            padding: 3px 8px;
            border-radius: 5px;
        }}
        .team-badge {{
            background: #ffffff;
            color: #000000;
            padding: 3px 8px;
            border-radius: 5px;
            font-size: 0.85em;
            font-weight: 600;
        }}
        .price {{
            color: #00ff87;
            font-weight: 600;
        }}
        .score {{
            background: linear-gradient(135deg, #00ff87 0%, #00d4aa 100%);
            color: #1a1a2e;
            padding: 5px 10px;
            border-radius: 8px;
            font-weight: bold;
        }}
        .footer {{
            text-align: center;
            padding: 30px;
            color: rgba(255, 255, 255, 0.5);
            font-size: 0.9em;
        }}
        .highlight-box {{
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
            border-radius: 15px;
            padding: 20px;
            margin-bottom: 25px;
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
            <h1>⚽ Fantasy Premier League</h1>
            <div class="subtitle">AI-Powered Player Recommendations</div>
        </div>
        
        {deadline_html}
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">⭐</span>
                <div>
                    <div class="section-title">Top 25 Forwards</div>
                    <div class="section-desc">Ranked by: xG/90, Form, Fixtures, Team Attack Strength, Bonus Potential</div>
                </div>
            </div>
            {self._df_to_html_table(spisser, 'FWD')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🎯</span>
                <div>
                    <div class="section-title">Top 25 Midfielders</div>
                    <div class="section-desc">Ranked by: xGI/90 (goals + assists), Creativity, Form, Fixtures</div>
                </div>
            </div>
            {self._df_to_html_table(midtbane, 'MID')}
        </div>
        
        <div class="section">
            <div class="section-header">
                <span class="section-icon">🛡️</span>
                <div>
                    <div class="section-title">Top 25 Defenders</div>
                    <div class="section-desc">xPts Model: 4×CS + 6×xG + 3×xA + MinPts + Bonus (adjusted for fixtures & playing time)</div>
                </div>
            </div>
            {self._df_to_html_table(forsvar, 'DEF')}
        </div>
        
        <div class="highlight-box">
            <h3>🔍 Arsenal Defense Spotlight: Saliba & Gabriel</h3>
            {self._df_to_html_table(arsenal_forsvar, 'DEF') if arsenal_forsvar is not None else '<p>Data not available</p>'}
        </div>
        
        <div class="footer">
            <p>Generated by FPL Analyzer • Data from Fantasy Premier League API</p>
            <p>Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
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


# Hovedprogram
if __name__ == "__main__":
    print("Starter FPL Analysator (Avansert versjon)...")
    print("-"*100)
    
    # Opprett analyzer
    analyzer = FPLAnalyzer()
    
    # Hent data
    if analyzer.hent_data():
        analyzer.hent_fixtures()  # Hent også fixture data
        analyzer.lag_spillerdataframe()
        
        # Generer HTML-rapport
        analyzer.generer_html_rapport("Fantasy_Premier_League_recommendations.html")
        
        # Vis også tekst-rapport i konsollen
        analyzer.vis_rapport()
    else:
        print("Kunne ikke hente data fra FPL API")
    
    print("\n\nEKSTRA FUNKSJONER:")
    print("-"*100)
    print("# Bygg anbefalt lag med budsjett:")
    print("analyzer.bygg_anbefalt_lag(budsjett=89.0)")
    print("\n# Sammenlign spesifikke spillere:")
    print("analyzer.sammenlign_spillere(['Haaland', 'Isak', 'Watkins'])")
    print("\n# Beste spillere per posisjon:")
    print("analyzer.beste_spisser_avansert(antall=15, min_minutter=180)")
    print("analyzer.beste_midtbanespillere(antall=15, min_minutter=180)")
    print("analyzer.beste_forsvarsspillere(antall=15, min_minutter=180)")
    print("\n# Spesialanalyser:")
    print("analyzer.beste_attacking_defenders(antall=10)  # Offensive backs")
    print("analyzer.finn_differentials(posisjon='DEF', maks_eierskap=10.0)")
    print("\n# Tilpass vekter for hver posisjon:")
    print("custom_vekter = {'clean_sheet_potential': 0.35, 'fixture_ease': 0.30, ...}")
    print("analyzer.beregn_avansert_forsvar_score(vekter=custom_vekter)")
