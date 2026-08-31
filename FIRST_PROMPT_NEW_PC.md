# Prvý prompt pre ChatGPT Desktop na novom PC

Skopíruj celý text pod čiarou ako prvú správu v novom projekte.

---

Pokračujeme vo vývoji projektu Europe Pulse. Lokálny Git repozitár je otvorený
v tomto workspace a jeho remote je `EPupdt/nws_ep`.

Najprv si kompletne prečítaj tieto súbory:

- `HANDOFF_NEW_PC.md`
- `README.md`
- `SOURCES.md`
- `.github/workflows/news-hub.yml`
- `config/policy.yml`
- `config/sources.yml`
- relevantné časti `src/news_hub/main.py`

Potom vykonaj iba read-only kontrolu stavu:

1. over pracovný strom, branch a remote;
2. vykonaj Fetch a zisti, či lokálna vetva nezaostáva za `origin/main`;
3. ak je čistá a iba zaostáva, bezpečne ju aktualizuj fast-forward/Pull;
4. vypíš posledných päť commitov;
5. zhrň aktuálnu architektúru, spúšťanie a verejné výstupy;
6. skontroluj, že v repozitári nie sú secrets.

Nepoužívaj `git reset --hard`, neprepisuj históriu, nemaž ani ručne neupravuj
prevádzkové dáta. Zachovaj cudzie alebo necommitnuté zmeny. API kľúče, heslá a
tokeny odo mňa nepýtaj do chatu; ak budú potrebné, veď ma ich bezpečným zadaním
priamo v príslušnej službe.

Diagnostika priameho Google Gemini API volania je uzavretá. Sanitizovaný log
potvrdil, že `gemini-2.5-flash-lite` vracia pre tento projekt 404, pretože už
nie je dostupný novým používateľom. Produkčné nastavenie používa
`gemini-3.5-flash-lite`; `openrouter/free` zostáva iba bezplatný fallback.
Collector zároveň bezpečne spracúva prázdny modelový obsah a prerušenú chunked
HTTP odpoveď (`IncompleteRead`) bez pádu celého workflow.

Po read-only kontrole mi podaj stručný stav a uveď rozdiely oproti handoffu.
Skontroluj prvé produkčné behy s Gemini 3.5; model, workflow ani WordPress ďalej
nemeň bez novej konkrétnej diagnostiky a vyhodnotenia.

Ďalší cieľ po stabilizácii News Hubu je audit a redesign `europepulse.eu` a
serverová WordPress integrácia verejného JSON feedu podľa handoffu. Existuje
WordPress admin konto `codex`, ale prihlasovacie údaje zadám iba priamo v
prehliadači.
