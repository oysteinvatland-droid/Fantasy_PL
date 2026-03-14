# ⚽ FPL Analyzer - Fantasy Premier League Spilleranalyse

Automatisk spilleranalyse for Fantasy Premier League med ukentlig e-postrapport.

## 🎯 Hva gjør dette?

Scriptet analyserer alle spillere i Fantasy Premier League og rangerer dem basert på:

- **Spisser**: xG per 90 min, form, fixtures, lagets angreps-styrke
- **Midtbanespillere**: xGI (mål+assist), creativity, form, fixtures
- **Forsvarsspillere**: Clean sheet-sannsynlighet, xG, xA, spilletid-sannsynlighet

### Forsvarsspiller-modell (xPts)

```
xPts = 4×CS + 6×xG + 3×xA + MinPts + Bonus
```

Hvor:
- **CS** = exp(-xGA_team) - Clean sheet sannsynlighet
- **xG** = Forventede mål per kamp
- **xA** = Forventede assists per kamp
- **MinPts** = Appearance points (2 hvis ≥60 min, 1 hvis <60 min)
- **Bonus** = Forventede bonuspoeng

Scoren justeres for:
- **Spilletid-sannsynlighet** (basert på siste 4 kamper)
- **Fixture difficulty** (neste 5 kamper)

## 📧 Automatisk ukentlig e-post

Repositoryet er satt opp med GitHub Actions som sender deg en FPL-rapport på e-post hver fredag kl 09:00 (norsk tid).

### Oppsett

1. **Fork dette repositoryet**

2. **Legg til secrets i GitHub**:
   
   Gå til: Settings → Secrets and variables → Actions → New repository secret
   
   Legg til følgende secrets:
   
   | Secret navn | Beskrivelse |
   |-------------|-------------|
   | `EMAIL_USERNAME` | Din Gmail-adresse (f.eks. `minmail@gmail.com`) |
   | `EMAIL_PASSWORD` | App-passord fra Google (se under) |
   | `EMAIL_TO` | E-postadressen du vil motta rapporten på |

3. **Opprett Gmail App-passord**:
   
   - Gå til [Google Account](https://myaccount.google.com/)
   - Security → 2-Step Verification (må være aktivert)
   - App passwords → Generate
   - Velg "Mail" og "Other" → Gi den navnet "FPL Analyzer"
   - Kopier det 16-tegns passordet og bruk det som `EMAIL_PASSWORD`

4. **Test manuelt**:
   
   Gå til: Actions → FPL Weekly Report → Run workflow


## ✅ Drifts-sjekkliste (GitHub Actions)

Hvis workflow kjører grønt men du ikke får e-post, sjekk dette:

1. **Secrets**
   - `EMAIL_USERNAME` og `EMAIL_PASSWORD` må være satt i repo-secrets.

2. **Firestore-tilgang**
   - Firestore-regler må tillate pipeline-kallene, ellers får du tomme abonnentlister eller tidlig stopp.
   - Se etter Firebase-feil i `Agent 1 – Data` loggen.

3. **Rapport-manifest**
   - Sjekk artifact `generated_reports.json` (ukentlig) / `welcome_reports.json` (velkomst).
   - Hvis filen er tom (`[]`), blir ingen e-post sendt.

4. **Deadline-vindu**
   - Ukentlig sending kjører i tidsvinduet (eller med `force_send=true` i manuelt run).


### 🧭 Runbook: Første 5 minutter ved feil

1. **Sjekk Agent 1 – Data**
   - Hvis jobben feiler med Firebase-feil, valider Firestore-regler/App Check og `FIREBASE_BEARER_TOKEN` secret.
2. **Sjekk artifacts**
   - `subscribers.json`, `generated_reports.json`, `welcome_reports.json`, `delivery_status.json`.
3. **Sjekk delivery-status**
   - `delivery_status.json` viser `reports`, `sent`, `failed`, `skipped`.
4. **Sjekk manuell testmodus**
   - Ved `workflow_dispatch` kan du bruke `dry_run=true` og `test_email_override` for trygg verifisering.
5. **Sjekk sikkerhetsoppsett**
   - Sett repo-variable `FIREBASE_REQUIRE_AUTH=true` når auth er konfigurert, slik at pipeline feiler tydelig ved manglende token.

## 🖥️ Lokal kjøring

### Installer avhengigheter

```bash
pip install -r requirements.txt
```

### Kjør scriptet

```bash
python fpl_analyzer.py
```

### Interaktiv modus

```bash
python -i fpl_analyzer.py
```

Etter at rapporten er vist, kan du kjøre:

```python
# Se detaljert beregning for en spiller
analyzer.vis_detaljert_beregning('Gabriel', posisjon='DEF')
analyzer.vis_detaljert_beregning('Saka', posisjon='MID')
analyzer.vis_detaljert_beregning('Haaland', posisjon='FWD')

# Sammenlign spillere
analyzer.vis_spillere(['Saliba', 'Gabriel', 'Van Dijk'], posisjon='DEF')
```

## 📊 Eksempel på output

```
====================================================================================================
FANTASY PREMIER LEAGUE - AVANSERT SPILLERANALYSE
====================================================================================================

⏰ TRANSFER DEADLINE - GAMEWEEK 24
   Deadline: Friday 24. January 2025 kl. 18:30
   Tid igjen: 2 dager, 14 timer, 23 minutter

⭐ TOPP 25 SPISSER - AVANSERT VURDERING
----------------------------------------------------------------------------------------------------
       name lag  pris  total  xg_per_90  form_num  fix_diff  team_str   ppm  bonus_per_kamp  total_points  valgt_prosent
    Haaland MCI  14.5   89.2       0.95       8.2       2.4      78.3  6.12            1.45           156           85.2
       Isak NEW  10.2   82.1       0.78       7.8       2.8      65.2  7.84            1.12           134           42.1
...
```

## 🔧 Tilpasning

### Endre schedule

Rediger `.github/workflows/fpl_weekly.yml`:

```yaml
schedule:
  - cron: '0 8 * * 5'  # Fredag kl 08:00 UTC
```

Cron-format: `minutter timer dag måned ukedag`

Eksempler:
- `'0 8 * * 5'` - Fredag kl 08:00
- `'0 18 * * 4'` - Torsdag kl 18:00
- `'0 8 * * 1,5'` - Mandag og fredag kl 08:00

### Endre antall spillere i rapporten

I `fpl_analyzer.py`, finn `vis_rapport()` og endre `antall=25` til ønsket antall.

## 📁 Filstruktur

```
fpl-analyzer/
├── .github/
│   └── workflows/
│       └── fpl_weekly.yml    # GitHub Actions workflow
├── fpl_analyzer.py           # Hovedscript
├── requirements.txt          # Python avhengigheter
└── README.md                 # Denne filen
```

## 🤝 Bidra

Forslag og forbedringer er velkomne! Opprett en issue eller pull request.

## 📝 Lisens

MIT License - Bruk fritt!
