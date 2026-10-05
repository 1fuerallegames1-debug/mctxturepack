"""Beispiel-Plugin: So bringst du Kai neue Fähigkeiten bei.

Jede .py-Datei in diesem Ordner wird beim Start automatisch geladen.
Kopiere diese Datei, ändere Name, Beschreibung und Code – fertig.
Dateien, deren Name mit "_" beginnt, werden ignoriert (z. B. zum Deaktivieren umbenennen).

Wichtig:
- Der Name muss eindeutig sein und darf nur a-z, 0-9 und _ enthalten.
- Die Beschreibung liest das KI-Modell, um zu entscheiden, wann es das Werkzeug nutzt.
  Auf Englisch funktioniert das mit den meisten Modellen am zuverlässigsten.
- confirm=True: Kai fragt vor jeder Ausführung um Erlaubnis (für alles, was etwas verändert!).
- Die Funktion bekommt immer zuerst "ctx" (Einstellungen, Arbeitsordner, ...) und gibt Text zurück.
"""

import json
import urllib.parse
import urllib.request

from kai.tools import ToolError, tool


@tool(
    "get_weather",
    "Get the current weather and a short forecast for a city.",
    {"city": {"type": "string", "description": "City name, e.g. 'Berlin'."}},
    required=["city"],
)
def get_weather(ctx, city: str):
    url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1&lang=de"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise ToolError(f"Wetterdienst nicht erreichbar: {e}") from None
    now = data["current_condition"][0]
    desc = (now.get("lang_de") or now.get("weatherDesc") or [{"value": "?"}])[0]["value"]
    lines = [f"Wetter in {city}: {desc}, {now['temp_C']} °C (gefühlt {now['FeelsLikeC']} °C), "
             f"Wind {now['windspeedKmph']} km/h, Luftfeuchte {now['humidity']} %"]
    for day in data.get("weather", [])[:3]:
        lines.append(f"{day['date']}: {day['mintempC']}–{day['maxtempC']} °C")
    return "\n".join(lines)
