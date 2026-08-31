# Europe Pulse — handoff na nový počítač

Aktualizované: 31. august 2026, Europe/Bratislava

Tento dokument je hlavný zdroj kontextu pre pokračovanie projektu v ChatGPT
Desktop na inom počítači. Repozitár je verejný, preto v ňom nesmú byť heslá,
API kľúče, WordPress prihlasovacie údaje ani cron-job.org token.

## 1. Cieľ projektu

Europe Pulse má byť rýchly anglický web o európskej politike, inštitúciách,
bezpečnosti, ekonomike, klíme a spoločnosti. Projekt má dve súvisiace časti:

1. **Europe Pulse News Hub** — automatizovaný monitoring médií, redakčný výber
   Europe Now/Top stories a verejný News Radar.
2. **EuropePulse.eu** — existujúci WordPress web, ktorý má prejsť auditom SEO,
   UX a vizuálu a následne bezpečne zobrazovať dáta News Hubu.

News Hub nie je systém na preberanie článkov. Pri externých médiách zobrazuje
iba titulok, vydavateľa, čas a priamy odkaz. Europe Now a Top stories používajú
vlastné stručné formulácie Europe Pulse.

## 2. Repozitár a verejné adresy

- Lokálny priečinok na pôvodnom PC: `C:\Projects\nws_ep`
- GitHub: `https://github.com/EPupdt/nws_ep`
- Predvolená vetva: `main`
- Remote: `https://EPupdt@github.com/EPupdt/nws_ep.git`
- GitHub Pages: `https://epupdt.github.io/nws_ep/`
- Verejný JSON: `https://epupdt.github.io/nws_ep/data/news-hub.json`
- Pages sa publikuje z `main` / `docs`.

Pri vytvorení tohto handoffu bol lokálny repozitár čistý a zosúladený s
`origin/main` na commite `bd66513`. Automatický agent však priebežne vytvára
prevádzkové commity, preto je tento hash iba orientačný. Na novom PC vždy
najprv použiť **Fetch origin** a potom **Pull origin**.

## 3. Aktuálna architektúra News Hubu

```text
cron-job.org každých 15 minút
        |
        v
GitHub workflow_dispatch
        |
        v
Python collector -> deduplikácia -> radar
        |
        +-> Google Gemini API (prvý pokus)
        |
        +-> OpenRouter free router (záloha)
        |
        v
data/state.json + audit JSONL + docs/data/news-hub.json
        |
        v
GitHub commit/push -> GitHub Pages -> WordPress integrácia
```

Dôležité:

- Jediným časovým spúšťačom je aktuálne **cron-job.org**.
- Workflow nemá interný GitHub `schedule` trigger.
- cron-job.org volá GitHub `workflow_dispatch` každých 15 minút.
- Python sám vyhodnocuje redakčné pásma v `Europe/Bratislava`.
- Workflow má `timeout-minutes: 20` a jednu concurrency skupinu.
- Ručné spustenie má voliteľný vstup `force`; iba `force=true` obíde časové
  pásma.
- GitHub Secrets sú uložené vzdialene a kopírovanie priečinka ich neovplyvní:
  `GEMINI_API_KEY` a `OR_API_KEY`.
- OpenRouter smie použiť iba `openrouter/free` alebo explicitný model s
  príponou `:free`. Nikdy nezapnúť platený model bez výslovného súhlasu.
- cron-job.org má vlastný GitHub token. Je uložený iba v cron-job.org a nesmie
  sa kopírovať do repozitára ani do chatu.

## 4. Harmonogram

Konfigurácia je v `config/policy.yml`:

- pracovné dni 05:30–08:30: každých 20 minút;
- pracovné dni 08:30–18:00: každých 10 minút;
- pracovné dni 18:00–23:50: každých 20 minút;
- víkend 05:30–22:00: každých 20 minút;
- mimo aktívnych pásiem agent skončí bez zberu.

Externý cron môže volať workflow každých 15 minút; samotná aplikácia rozhodne,
či je reálny zber splatný.

## 5. Dôležité súbory

- `.github/workflows/news-hub.yml` — jediný GitHub Actions workflow.
- `src/news_hub/main.py` — zber, stav, LLM volanie a publikovanie.
- `src/news_hub/convergence.py` — modelovo nezávislé developing clusters.
- `src/news_hub/archive.py` — append-only redakčný archív a 14-dňová história.
- `config/policy.yml` — modely, limity, časové pásma a počty výstupov.
- `config/sources.yml` — zdroje a režim publikovania.
- `SOURCES.md` — licenčná a publikačná politika.
- `data/state.json` — krátkodobý prevádzkový stav.
- `data/selection_logs/*.jsonl` — technický audit behov.
- `data/archive/*.jsonl` — obsahový archív publikovaných výberov.
- `docs/data/news-hub.json` — verejný dátový kontrakt.
- `docs/index.html` — GitHub Pages dashboard.

Súbory v `data/`, `docs/data/` a mesačné JSONL sú prevažne generované. Pred
ručnou úpravou treba overiť, či ide o zdrojový súbor alebo prevádzkový výstup.
Automatické dáta sa nemajú spätne prepisovať bez jasného dôvodu.

## 6. Aktuálne zdroje

Aktívne spravodajské zdroje:

- Deutsche Welle Europe
- BBC News Europe
- France 24 English
- POLITICO Europe
- EUobserver
- EL PAÍS English
- Notes from Poland

Analytické zdroje:

- European Council on Foreign Relations (ECFR)
- Bruegel

Euractiv je zámerne vypnutý, pretože jeho RSS a sitemap vracajú cloudovým
collectorom HTTP 403. Ochranu neobchádzať. Financial Times, Bloomberg a Le
Monde nie sú integrované pre licenčné/paywall obmedzenia. Think-tank obsah
nesmie vytvárať Europe Now ani dominovať live news.

## 7. Rozpracovaná diagnostika Google Gemini

Toto je najbližšia technická priorita. Pred zmenou modelu treba dokončiť
diagnostiku.

Aktuálne nastavenie:

```yaml
models:
  gemini: gemini-2.5-flash-lite
  openrouter:
    - openrouter/free
```

Kód skúša Google Gemini priamo ako prvý. Až pri zlyhaní prejde na OpenRouter.
Audit `data/selection_logs/2026-08.jsonl` však ukazuje, že úspešné LLM výbery
pravidelne zapisujú `openrouter:openrouter/free`, nie Gemini. Google AI Studio
Usage pre projekt **Europe Pulse News Hub** ukazoval prevažne:

- `404 Not Found`;
- menší počet `429 Too Many Requests`;
- prakticky žiadne výstupné tokeny;
- požiadavky sa po časti obdobia prestali priraďovať ku konkrétnemu modelu.

Z toho vyplýva, že Gemini volanie sa nedokončuje a systém používa fallback.
Model `gemini-2.5-flash-lite` aj REST tvar `/v1beta/models/{model}:generateContent`
sú podľa aktuálnej dokumentácie platné. API kľúč Google prijíma; dominantná 404
nie je typický prejav chýbajúceho kľúča.

Ručný PowerShell test zatiaľ nepriniesol spoľahlivé telo odpovede. Pri
kopírovaní sa URL raz zmenila na Markdown odkaz a neskôr na `https\://`, takže
výsledok ručného testu sa nesmie považovať za definitívny dôkaz konkrétnej
príčiny 404.

### Odporúčaný ďalší diagnostický krok

1. Bez zmeny produkčného modelu overiť cez ten istý kľúč `models.list`, či
   projekt model `gemini-2.5-flash-lite` vôbec vidí.
2. Vykonať minimálne `generateContent` volanie so správnou URL a zachytiť celé
   bezpečné telo chyby bez API kľúča.
3. Ak lokálny test nie je možný, doplniť do agenta sanitizovanú diagnostiku:
   provider, model, HTTP status a maximálne krátke telo chyby. Nikdy nelogovať
   URL s `?key=`, hlavičku ani samotný secret.
4. Až podľa presnej správy rozhodnúť medzi zmenou API verzie, modelu, kľúča,
   projektu alebo retry stratégie.

Sekundárny problém: `openrouter/free` vyberá model náhodne. Niektoré odpovede
mali neprimerane veľa výstupných tokenov a aktuálna validácia dostatočne
nekontroluje vnútorný typ `article_ids`. Neštandardný JSON môže prejsť prvou
kontrolou a zlyhať neskôr. Plánovaná oprava po diagnostike Gemini:

- limit výstupu približne 2 000 tokenov;
- plná validácia `article_ids`;
- žiadna chybná LLM odpoveď nesmie zhodiť collector;
- Gemini ako primárny model, OpenRouter iba bezplatná posledná záloha alebo
  úplné vypnutie fallbacku podľa rozhodnutia vlastníka.

## 8. WordPress EuropePulse.eu — cieľ a hranice

WordPress kód zatiaľ nie je v tomto repozitári. Existuje samostatné WordPress
admin konto `codex`; vlastník sa doň prihlási sám. Heslo sa nesmie zapisovať do
handoffu ani chatu.

Pred implementáciou treba vykonať audit:

- aktívna téma/child theme, pluginy, builder, cache/CDN a bezpečnostné pluginy;
- záloha a možnosť rollbacku;
- URL/permalinky, indexácia, robots, sitemap, canonical, redirects a 404;
- title/meta, headingy, schema, Open Graph, interné odkazy a archívy;
- Core Web Vitals, mobil, klávesnica, kontrast a čitateľnosť;
- informačná architektúra a jasnosť redakčnej ponuky.

News Hub má byť pripojený serverovo cez ľahký vlastný WordPress plugin:

- načítať verejný JSON server-side;
- cache cez WordPress Transients 10–15 minút;
- pri výpadku používať poslednú platnú cache;
- chyba feedu nesmie rozbiť homepage;
- sanitizovať text a URL;
- externé odkazy: `target="_blank" rel="noopener noreferrer"`;
- nevytvárať automaticky duplicitné WordPress články;
- third-party radar neoznačovať schema typom NewsArticle;
- originálne WordPress články majú zostať hlavným SEO aktívom.

Vizuálny cieľ: pokojný, moderný, dôveryhodný európsky briefing; silná typografia,
prehľadná hierarchia, mobile-first, bez clickbaitu a vizuálneho chaosu.

## 9. Prenos priečinka na nový počítač

### Na pôvodnom PC

1. Nechať dokončiť prípadnú lokálnu operáciu Git/GitHub Desktop.
2. V GitHub Desktop použiť **Fetch origin** a **Pull origin**.
3. Overiť, že nie sú necommitnuté zmeny.
4. Zavrieť editor, ChatGPT Desktop a GitHub Desktop.
5. Skopírovať celý priečinok `C:\Projects\nws_ep`, vrátane skrytého `.git`.

Kopírovanie `.git` je zásadné. Bez neho je to iba kolekcia súborov, nie lokálny
repozitár.

### Na novom PC

1. Umiestniť priečinok napríklad opäť do `C:\Projects\nws_ep`.
2. Prihlásiť GitHub Desktop ako `EPupdt`.
3. GitHub Desktop: **File -> Add local repository** a vybrať skopírovaný
   priečinok. Neklonovať druhú kópiu cez existujúci priečinok.
4. Skontrolovať repository `EPupdt/nws_ep`, branch `main`.
5. Použiť **Fetch origin** a podľa potreby **Pull origin**. Cron môže počas
   presunu vytvoriť nové vzdialené commity.
6. Ak GitHub Desktop ponúkne publikovať nový repozitár, zastaviť sa — znamená
   to, že nevidí `.git` alebo bol vybraný nesprávny priečinok.
7. V ChatGPT Desktop otvoriť priamo lokálny priečinok projektu ako workspace.
8. Poslať prvý prompt zo súboru `FIRST_PROMPT_NEW_PC.md`.

API kľúče, GitHub Actions secrets, Pages nastavenie a cron-job.org zostávajú
online. Netreba ich kopírovať ani vytvárať znova. Lokálny beh bez kľúčov je
možný iba v obmedzenom režime; secrets sa nemajú sťahovať z GitHubu.

## 10. Prvá kontrola na novom PC

Nový ChatGPT Desktop má najprv vykonať iba read-only kontrolu:

```powershell
git status -sb
git remote -v
git branch --show-current
git log --oneline -5
```

Očakávané:

- branch `main`;
- remote smeruje na `EPupdt/nws_ep`;
- pracovný strom je čistý;
- lokálna vetva nie je za `origin/main` po vykonaní Pull.

Nesmie používať `git reset --hard`, prepisovať generované dáta, meniť model,
mazať históriu ani vytvárať nové secrets bez výslovného súhlasu.

## 11. Definition of done pre pokračovanie

Najbližšia etapa je hotová, keď:

1. je presne vysvetlené, prečo Google vracia 404/429;
2. Gemini funguje ako stabilný primárny model alebo je zdokumentovaný dôvod,
   prečo ho nemožno použiť;
3. chybná LLM odpoveď nikdy nezhodí celý GitHub Action;
4. bezplatnosť OpenRouter fallbacku je technicky vynútená;
5. WordPress audit je zdokumentovaný pred zásadnými zmenami;
6. integrácia JSON je serverová, cacheovaná a odolná voči výpadku;
7. redesign chráni existujúce URL, SEO a obsah;
8. existuje changelog a rollback postup.

