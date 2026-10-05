# Kai – deine eigene KI auf deinem PC

Kai ist ein KI-Assistent, der **komplett auf deinem eigenen Computer** läuft.
Er braucht kein Abo und keinen API-Schlüssel, und deine Daten verlassen den PC nicht
(außer du lässt ihn im Internet suchen).
Kai antwortet nicht nur, er **erledigt Dinge für dich**: Er führt Befehle aus, verwaltet Dateien,
sucht im Internet, öffnet Programme und merkt sich, was du ihm sagst.

Damit dabei nichts schiefgeht, **fragt Kai vor jeder Aktion, die etwas verändert, um Erlaubnis.**

```
Du › Räum meinen Download-Ordner auf und sortier die PDFs in einen Unterordner "PDFs"
  ⚙ list_directory  path="C:\Users\Alex\Downloads"
  ✓ Inhalt von C:\Users\Alex\Downloads (34 Einträge): ...
  ┌ Kai möchte Folgendes tun:
  │ PowerShell-Befehl:
  │ New-Item -ItemType Directory -Force "$HOME\Downloads\PDFs"; Move-Item "$HOME\Downloads\*.pdf" "$HOME\Downloads\PDFs"
  └ Erlauben? [j] ja  [n] nein  [i] immer erlauben (für diese Sitzung): j
  ✓ Exit-Code: 0
Kai › Erledigt! Ich habe 12 PDF-Dateien nach Downloads\PDFs verschoben.
```

---

## Was Kai kann

| Fähigkeit | Werkzeug | Fragt vorher? |
|---|---|---|
| Befehle ausführen (PowerShell bzw. bash): Dateien verschieben, Programme installieren, Netzwerk prüfen … | `run_command` | ja (gefährliche Befehle **immer**) |
| Python-Code schreiben und ausführen (Rechnen, Daten umwandeln, Diagramme …) | `run_python` | ja |
| Dateien lesen (auch Word-Dokumente `.docx`) | `read_file` | nein |
| Dateien schreiben (vorher wird automatisch eine Sicherungskopie angelegt) | `write_file` | ja |
| Ordner anzeigen und Dateien suchen | `list_directory`, `find_files` | nein |
| Programme, Dateien, Ordner und Webseiten öffnen | `open_item` | ja (außer bekannte Webseiten) |
| Im Internet suchen und Webseiten lesen | `web_search`, `fetch_webpage` | nur bei unbekannten Adressen |
| Datum, Uhrzeit, PC-Infos (RAM, Speicherplatz, Ordner …) | `system_info` | nein |
| Sich Dinge dauerhaft merken und wieder vergessen | `remember`, `forget` | nein |
| Wetter (Beispiel-Plugin, siehe unten) | `get_weather` | nein |

Mit [eigenen Plugins](#eigene-fähigkeiten-plugins) kannst du Kai beliebig erweitern.

---

## Installation (Windows, ca. 15 Minuten)

### 1. Python installieren
Lade Python von <https://www.python.org/downloads/> herunter und installiere es.
**Wichtig:** Im ersten Fenster des Installers den Haken bei **„Add python.exe to PATH“** setzen.

### 2. Ollama installieren
Ollama ist das Programm, das das KI-Modell auf deinem PC ausführt.
Lade es von <https://ollama.com/download> herunter und installiere es.
Danach läuft Ollama im Hintergrund (Lama-Symbol unten rechts in der Taskleiste).

### 3. Kai herunterladen
Lade dieses Repository herunter (grüner Knopf **Code → Download ZIP**) und entpacke es,
zum Beispiel nach `C:\Users\<Name>\Kai`. Du brauchst nur den Ordner `ki-assistent`.

### 4. Kai starten
Doppelklick auf eine dieser Dateien:

- **`start-web.bat`**: Kai im **Browser** (empfohlen, schöner). Das schwarze Fenster offen lassen.
- **`start.bat`**: Kai direkt im **Terminal**.

Beim ersten Start fragt Kai, ob er das KI-Modell herunterladen soll (einmalig, ca. 5 GB).
Alternativ geht das auch selbst in der Eingabeaufforderung: `ollama pull qwen3:8b`

> **macOS / Linux:** Python 3 und Ollama installieren, dann im Ordner `ki-assistent`
> `./start.sh` (Terminal) bzw. `./start.sh --web` (Browser) ausführen.

---

## Welches Modell passt zu meinem PC?

Das „Modell“ ist das eigentliche Gehirn. Größere Modelle sind klüger, brauchen aber mehr
Grafikspeicher (VRAM). Wie viel du hast, zeigt der Task-Manager → Leistung → GPU → „Dedizierter GPU-Speicher“.

| Grafikkarte | Empfohlenes Modell | Herunterladen mit |
|---|---|---|
| keine / unter 6 GB | `qwen3:4b` (läuft auch auf dem Prozessor, dann aber langsam) | `ollama pull qwen3:4b` |
| 8 GB | `qwen3:8b` (**Standard**) | `ollama pull qwen3:8b` |
| 12 GB | `qwen3.5` | `ollama pull qwen3.5` |
| 16 GB und mehr | `gemma4` | `ollama pull gemma4` |

Modell wechseln: in Kai `/modell gemma4` eingeben bzw. oben im Browser auswählen
(oder `"modell"` in `config.json` ändern).

**Wichtig:** Das Modell muss **Werkzeuge (Tools)** unterstützen, sonst kann Kai nur reden, aber nichts tun.
Eine Liste geeigneter Modelle gibt es unter <https://ollama.com/search?c=tools>.

---

## Benutzung

Schreib Kai einfach, was du willst, ganz normal auf Deutsch. Ein paar Ideen:

- „Was ist auf meinem Desktop?“
- „Wie viel Speicherplatz habe ich noch? Was sind die 10 größten Dateien in Downloads?“
- „Such im Internet, was die neueste Minecraft-Version ist.“
- „Öffne Spotify.“ / „Öffne YouTube im Browser.“
- „Lies Bewerbung.docx und mach Verbesserungsvorschläge.“
- „Schreib mir ein Python-Skript, das alle Fotos in diesem Ordner nach Datum umbenennt, und führ es aus.“
- „Erstelle eine Einkaufsliste als Textdatei auf dem Desktop.“
- „Merk dir, dass mein Minecraft-Server unter D:\Server liegt.“
- „Starte meinen Minecraft-Server im Hintergrund.“

### Befehle im Terminal

| Befehl | Wirkung |
|---|---|
| `/neu` | neues Gespräch (das Langzeitgedächtnis bleibt) |
| `/modelle`, `/modell <name>` | installierte Modelle anzeigen / Modell wechseln |
| `/gedaechtnis`, `/vergiss <nr>` | Gemerktes anzeigen / einen Eintrag löschen |
| `/auto an` / `/auto aus` | Aktionen ohne Nachfrage ausführen / wieder nachfragen |
| `/hilfe`, `/beenden` | Hilfe / Kai beenden |
| `Strg+C` | laufende Aufgabe abbrechen |

Im Browser gibt es dafür Knöpfe: Modellauswahl, „Automatik“, „Neuer Chat“ und „Stopp“.

---

## Sicherheit: bitte lesen

Kai kann auf deinem PC wirklich Dinge tun. Deshalb gibt es mehrere Schutzmechanismen:

1. **Nachfrage vor Aktionen.** Befehle, Python-Code, Datei-Änderungen und das Öffnen von Programmen
   werden dir vorher angezeigt und erst nach deinem „Ja“ ausgeführt. **Lies dir an, was Kai tun will.**
   KI-Modelle machen Fehler.
2. **„Immer erlauben“** gilt nur für das eine Werkzeug und nur bis zum Beenden von Kai.
3. **Gefährliche Befehle** (Löschen ganzer Ordner, Formatieren, Herunterfahren, Registry löschen …)
   werden **immer** nachgefragt, auch im Automatik-Modus.
4. **Sicherungskopien:** Bevor Kai eine Datei überschreibt, legt er die alte Version in
   `daten/sicherungen/` ab.
5. **Schutz vor manipulierten Webseiten/Dateien:** Inhalte aus dem Internet oder aus Dateien behandelt Kai
   als Daten, nicht als Befehle. Ruft Kai eine Webadresse auf, die weder von dir noch aus einem
   Suchergebnis stammt, fragt er vorher nach. So kann eine präparierte Seite keine Daten von dir
   über einen Link „hinausschmuggeln“.
6. **Browser-Oberfläche nur lokal:** Der Webserver ist nur auf deinem PC erreichbar (`127.0.0.1`) und durch
   einen zufälligen Zugangsschlüssel geschützt, den nur der automatisch geöffnete Link enthält.

**Tipps:** Starte Kai nicht als Administrator. Den Automatik-Modus nur verwenden, wenn du genau weißt,
was du tust.

---

## Einstellungen (`config.json`)

Beim ersten Start wird `config.json` aus `config.beispiel.json` erstellt. Öffne sie mit dem Editor:

| Einstellung | Bedeutung | Standard |
|---|---|---|
| `name` | Name des Assistenten | `"Kai"` |
| `dein_name` | dein Name (Kai spricht dich dann damit an) | `""` |
| `sprache` | Antwortsprache | `"Deutsch"` |
| `modell` | KI-Modell | `"qwen3:8b"` |
| `kontext_laenge` | Wie viel vom Gespräch das Modell gleichzeitig „im Kopf“ hat (Tokens). Mehr = besseres Gedächtnis, braucht aber mehr Speicher. Bei Speicherfehlern verkleinern (z. B. `8192`), bei viel VRAM vergrößern (`32768`). | `16384` |
| `temperatur` | Kreativität (0 = sachlich, 1 = kreativ) | `0.6` |
| `denken` | `null` = Standard des Modells, `false` = schneller ohne Nachdenkphase, `true` = gründlicher | `null` |
| `denken_anzeigen` | Gedanken des Modells im Terminal anzeigen | `false` |
| `bestaetigung` | `"nachfragen"` (empfohlen) oder `"automatisch"` | `"nachfragen"` |
| `immer_erlauben` | Werkzeuge, die nie nachfragen, z. B. `["open_item"]` | `[]` |
| `deaktivierte_werkzeuge` | Werkzeuge abschalten, z. B. `["run_python"]` | `[]` |
| `max_schritte` | maximale Werkzeug-Schritte pro Aufgabe | `25` |
| `befehl_timeout` | Zeitlimit für Befehle in Sekunden | `120` |
| `arbeitsordner` | Standardordner für Befehle (`""` = Benutzerordner) | `""` |
| `websuche` | `{"anbieter": "duckduckgo"}` oder eine eigene [SearXNG](https://docs.searxng.org/)-Instanz: `{"anbieter": "searxng", "searxng_url": "http://localhost:8888"}` | DuckDuckGo |
| `web_port` | Port der Browser-Oberfläche | `8765` |
| `zusatz_anweisungen` | Eigene Regeln/Persönlichkeit, z. B. `"Sprich wie ein Pirat. Antworte immer kurz."` | `""` |
| `anbieter`, `server_url`, `api_schluessel` | siehe [Andere KI-Programme](#andere-ki-programme-lm-studio-llamacpp-) | Ollama |

Nach Änderungen Kai neu starten.

---

## Eigene Fähigkeiten (Plugins)

Jede `.py`-Datei im Ordner `plugins` wird beim Start geladen. Ein vollständiges Beispiel ist
[`plugins/beispiel_wetter.py`](plugins/beispiel_wetter.py). Das Grundgerüst:

```python
from kai.tools import tool

@tool(
    "turn_on_lights",                                     # eindeutiger Name
    "Turn the smart lights in a room on or off.",         # wann soll die KI das nutzen? (Englisch klappt am besten)
    {"room": {"type": "string", "description": "Room name"},
     "on": {"type": "boolean", "description": "true = on, false = off"}},
    required=["room", "on"],
    confirm=True,                                         # vorher um Erlaubnis fragen
)
def turn_on_lights(ctx, room, on):
    # ... hier dein Code (z. B. Anfrage an deine Smart-Home-Zentrale) ...
    return f"Licht im {room} ist jetzt {'an' if on else 'aus'}."
```

Du kannst dir Plugins übrigens auch von Kai selbst schreiben lassen: „Schreib mir ein Plugin für Kai, das …“

---

## Andere KI-Programme (LM Studio, llama.cpp …)

Statt Ollama funktioniert jeder Server mit OpenAI-kompatibler Schnittstelle, z. B. **LM Studio**
(dort den „Local Server“ starten):

```json
"anbieter": "openai",
"server_url": "http://localhost:1234/v1",
"modell": "name-des-geladenen-modells"
```

Die Kontextlänge stellst du dann im jeweiligen Programm ein (nicht in `config.json`).

---

## Probleme und Lösungen

| Problem | Lösung |
|---|---|
| „Keine Verbindung zum KI-Server“ | Ollama starten (Startmenü → Ollama). Prüfen: <http://localhost:11434> im Browser muss „Ollama is running“ zeigen. |
| „Python wurde nicht gefunden“ | Python neu installieren und **„Add python.exe to PATH“** anhaken. |
| „Das Modell unterstützt keine Werkzeuge“ | Ein Modell aus der Tabelle oben verwenden. |
| Kai ist sehr langsam | Kleineres Modell wählen, `"denken": false` setzen, `kontext_laenge` verkleinern. Mit `ollama ps` sieht man, ob das Modell auf der GPU läuft. |
| Speicherfehler / „out of memory“ | `kontext_laenge` verkleinern (z. B. `8192`) oder kleineres Modell. |
| Kai vergisst, was ich vorhin gesagt habe | `kontext_laenge` vergrößern. Für Dauerhaftes: „Merk dir …“. |
| Kai behauptet etwas, ohne es zu prüfen | Sag ausdrücklich „Prüf das nach“ oder „Such im Internet“. Größere Modelle sind deutlich zuverlässiger. |
| Websuche findet nichts | DuckDuckGo blockiert manchmal automatische Anfragen. Später erneut versuchen oder eine SearXNG-Instanz eintragen. |
| Browser zeigt „Kein gültiger Zugangsschlüssel“ | Kai immer über `start-web.bat` öffnen: Der Link enthält den Schlüssel. |

---

## Ehrliche Grenzen

- Lokale Modelle sind kleiner als ChatGPT oder Claude. Sie verstehen Aufgaben manchmal falsch oder
  erfinden Dinge. Je größer das Modell, desto besser.
- Kai sieht deinen Bildschirm nicht und kann keine Maus steuern. Er arbeitet über Befehle, Dateien und Programme.
- Ohne Grafikkarte läuft alles, aber Antworten können dann eine Minute oder länger dauern.

---

## Für Bastler

- **Ohne Zusatzpakete:** nur die Python-Standardbibliothek (Python 3.9+).
- **Tests ausführen:** `python -m unittest discover -s tests -t .` (simuliert einen KI-Server, Ollama wird dafür nicht gebraucht)
- **Aufbau:**

```
ki-assistent/
├── start.bat / start-web.bat / start.sh   Startdateien
├── config.beispiel.json                   Vorlage für config.json
├── plugins/                               eigene Werkzeuge
├── daten/                                 Gedächtnis, Sicherungskopien, Logs (wird automatisch angelegt)
└── kai/
    ├── agent.py      Gesprächsverlauf, Anweisungen an das Modell, Werkzeug-Schleife, Nachfragen
    ├── llm.py        Verbindung zu Ollama bzw. OpenAI-kompatiblen Servern (Streaming, Tool-Calls)
    ├── cli.py        Terminal-Oberfläche
    ├── web.py        Browser-Oberfläche (Server) + static/index.html
    ├── config.py     Einstellungen
    └── tools/        eingebaute Werkzeuge (system, files, web, memory)
```
