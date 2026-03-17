# Kodeanalyserapport – Fantasy PL Pipeline

**Dato:** 2026-03-17
**Analysert av:** Claude (claude-sonnet-4-6)

---

## Sammendrag

Prosjektet er en batch-pipeline som sender personlige FPL-analyserapporter på e-post til abonnenter. Koden er godt strukturert i fire separate agenter med tydelig ansvarsfordeling, og har et solid fundament. Nedenfor følger en detaljert vurdering av struktur, kvalitet og forbedringsområder.

---

## Arkitektur og struktur

### Styrker

**Klar separasjon av ansvar (4-agent pipeline):**
```
agent_data.py      → Datahenting (FPL API + Firestore)
agent_analyse.py   → Beregning og scoring
agent_rapport.py   → HTML-rapportgenerering
agent_levering.py  → E-postutsending + Firebase-oppdatering
```
Hver agent har klart definert input/output via JSON-filer som mellomlagres. Dette gjør det enkelt å debugge, kjøre enkeltledd på nytt og teste isolert.

**Delt kodebibliotek (`pipeline_shared/`):**
- `firestore.py` – Firestore-kommunikasjon med god typeannotering og `from __future__ import annotations`
- `io.py` – Konsistent filhåndtering med `Path`, utf-8 og default-verdi

**GitHub Actions-integrasjon:**
- To separate workflows (`fpl_weekly.yml`, `fpl_test.yml`)
- Unit tests kjøres som første steg i CI-pipeline
- Artifakt-overlevering mellom job-steg er gjennomtenkt

---

## Kodekvalitet

### Positivt

| Område | Vurdering |
|--------|-----------|
| Lesbarhet | God – norske funksjonsnavn er konsistente og beskrivende |
| Feilhåndtering | Dekker de viktigste feilscenarioene (HTTP-feil, manglende filer, Firebase-feil) |
| Parallellisering | `ThreadPoolExecutor` brukes korrekt for batch-henting av spillerhistorikk |
| Typer | `pipeline_shared/` bruker type hints og `dict[str, Any]` konsistent |
| Testdekning | Tester for pipeline-kontrakt, Firestore-parsing og e-postsending via monkeypatching |

### Svakheter og risikopunkter

#### 1. SSL-verifisering deaktivert globalt (høy risiko)
**Fil:** `agent_data.py:26`, `fpl_analyzer.py:10`
```python
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
```
SSL-warnings er slått av globalt, og `verify=False` brukes i `hent_fixtures()` og `hent_spiller_historikk()`. `hent_fpl_bootstrap()` prøver med SSL først og faller tilbake, men de andre funksjonene hopper rett til `verify=False`. Dette er et sikkerhetsavvik – man bør bruke `certifi` eller spesifisere CA-bundle.

#### 2. Firestore-autentisering er valgfri
**Fil:** `pipeline_shared/firestore.py` (implisitt via `FIREBASE_REQUIRE_AUTH`)
```python
# firestore_headers() returnerer {} hvis token mangler og REQUIRE_AUTH=false
```
Uten bearer token sendes Firestore-kall uten autentisering. I produksjon må `FIREBASE_REQUIRE_AUTH=true` settes eksplisitt. En standardverdi på `true` ville vært sikrere.

#### 3. Duplisert logikk mellom `agent_data.py` og `fpl_analyzer.py`
**Fil:** `agent_data.py:62-87` vs `fpl_analyzer.py:22-60`
Funksjonen `hent_spiller_historikk()` er nærmest identisk implementert to steder. `fpl_analyzer.py` har den samme logikken med caching, men brukes nå kun som «render-motor» via `agent_rapport.py`. Disse bør samles i `pipeline_shared/`.

#### 4. `fpl_analyzer.py` er svært stor (173 KB / ~4000+ linjer)
Filen inneholder både dataanalyse, API-kall og HTML-generering i én monolitt. Den er vanskelig å teste isolert og bryter med single responsibility-prinsippet. `agent_rapport.py` importerer kun HTML-genereringsdelen, men hele filen lastes inn.

#### 5. Hardkodet avsenderadresse
**Fil:** `agent_levering.py:73`
```python
def send_rapporter(reports, emne, fra="FPL Analyse <kontakt@fplanalyse.no>"):
```
E-postadressen er hardkodet som standardverdi. Bør hentes fra miljøvariabel for å gjøre koden mer fleksibel og enklere å teste i staging-miljø.

#### 6. `agent_levering.py:163` – `write_json` importeres ikke
```python
write_json('delivery_status.json', {'mode': 'welcome', ...})
```
`write_json` brukes men importeres ikke i `agent_levering.py`. Dette vil kaste `NameError` ved kjøring av welcome-modus uten rapporter. Kun `read_json` er importert.

#### 7. `send_rapporter()` returnerer kun `sendt`-tall (ikke tuple)
**Fil:** `agent_levering.py:69, 179`
```python
sendt = send_rapporter(reports, config['subject'])
```
Funksjonen returnerer `(sendt, feil)` tuple (linje 111), men kallestedet behandler det som ett tall. `sendt` vil da bli en tuple, og `if sendt == 0` vil aldri treffe. Dette er en latent bug.

#### 8. Manglende `__main__`-guard i `agent_analyse.py`
Koden er pakket i `if __name__ == "__main__":`, men hjelpefunksjoner som `bygg_dataframes()` og score-funksjonene er ikke importerbare uten sideeffekter. Hadde disse vært i en modul, ville `agent_rapport.py` kunnet gjenbruke dem direkte istedenfor å importere `FPLAnalyzer`.

#### 9. `warnings.filterwarnings('ignore')` i `agent_analyse.py:21`
Alle warnings undertrykkes globalt. Bør begrenses til spesifikke advarseltyper (f.eks. pandas `SettingWithCopyWarning`).

---

## Testdekning

| Test | Dekker |
|------|--------|
| `test_pipeline_contract.py` | Rapportstruktur, SMTP-sending (mocked), Firestore-parsing |
| `test_observability_security.py` | Firestore HTTP-feil, autentisering |
| `conftest.py` | sys.path-oppsett |

**Mangler:**
- Tester for `agent_analyse.py` (scoring-logikk, normalisering, fixture difficulty)
- Tester for `sjekk_deadline()` i `agent_data.py`
- Integrasjonstest for agent_rapport (HTML-generering)
- Tester for edge case: ingen aktive fixtures

---

## Avhengigheter (`requirements.txt`)

```
requests>=2.28.0
pandas>=1.5.0
numpy>=1.23.0
pytest>=7.0.0
```

- Svært minimalistisk – bra for enkle deployments
- Mangler versjonslocking (`pip freeze`/`poetry.lock`/`requirements-lock.txt`)
- `smtplib`, `json`, `concurrent.futures` er stdlib – trenger ikke listes

---

## Oppsummering – Prioritert liste

| Prioritet | Problem | Fil |
|-----------|---------|-----|
| 🔴 Kritisk | `write_json` ikke importert i `agent_levering.py` | `agent_levering.py:163` |
| 🔴 Kritisk | `send_rapporter` returnerer tuple men behandles som int | `agent_levering.py:69,179` |
| 🟠 Høy | SSL `verify=False` hardkodet i flere funksjoner | `agent_data.py`, `fpl_analyzer.py` |
| 🟠 Høy | Firestore auth er opt-in, burde være opt-out | `pipeline_shared/firestore.py` |
| 🟡 Medium | Duplisert historikk-logikk | `agent_data.py` vs `fpl_analyzer.py` |
| 🟡 Medium | Hardkodet avsender-e-post | `agent_levering.py:73` |
| 🟡 Medium | `fpl_analyzer.py` er en monolitt (173 KB) | `fpl_analyzer.py` |
| 🟢 Lav | Manglende tester for scoring-logikk | `agent_analyse.py` |
| 🟢 Lav | Global `warnings.filterwarnings('ignore')` | `agent_analyse.py:21` |
| 🟢 Lav | Versjonslocking av avhengigheter | `requirements.txt` |
