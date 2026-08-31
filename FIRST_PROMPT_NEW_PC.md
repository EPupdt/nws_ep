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

Aktuálna priorita je dokončiť diagnostiku priameho Google Gemini API volania.
Kód skúša `gemini-2.5-flash-lite` ako prvý model, ale audit ukazuje pravidelný
fallback na `openrouter/free`. Google AI Studio Usage ukázal prevažne 404 a
občas 429. Pred zmenou modelu alebo produkčného workflow treba bezpečne získať
presné telo chyby Google a overiť dostupnosť modelu cez `models.list`. Doterajší
ručný PowerShell test bol skreslený formátovaním kopírovanej URL, preto jeho
404 nepovažuj za definitívnu správu o príčine.

Po read-only kontrole mi podaj stručný stav, uveď zistené rozdiely oproti
handoffu a navrhni najmenší bezpečný diagnostický postup. Produkčný model,
workflow ani WordPress zatiaľ nemeň, kým diagnostiku spolu nevyhodnotíme.

Ďalší cieľ po stabilizácii News Hubu je audit a redesign `europepulse.eu` a
serverová WordPress integrácia verejného JSON feedu podľa handoffu. Existuje
WordPress admin konto `codex`, ale prihlasovacie údaje zadám iba priamo v
prehliadači.

