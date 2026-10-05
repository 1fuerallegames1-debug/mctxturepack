"""Offline-Spracherkennung für Angel (läuft auf dem PC, ohne Internet-Dienst).

Verwendet Vosk (ein kleines, lokales Spracherkennungs-Modell). Beim ersten Mal wird das deutsche
Modell einmalig heruntergeladen. Für das Mikrofon wird `sounddevice` genutzt.

Beides sind Zusatzpakete. Fehlen sie, erklärt Angel genau, wie man sie installiert – alles andere
(Chat, Werkzeuge) funktioniert ohne sie weiter.
"""

from __future__ import annotations

import json
import queue
import threading
import urllib.request
import zipfile
from pathlib import Path

SAMPLE_RATE = 16000
PIP_HINT = ("Für die Spracheingabe fehlen Pakete. Installiere sie einmalig in der Eingabeaufforderung:\n"
            "    pip install vosk sounddevice\n"
            "Danach Angel neu starten.")


class VoiceError(Exception):
    """Verständliche Meldung, wenn die Spracheingabe (noch) nicht geht."""


def available() -> tuple[bool, str]:
    """(geht, meldung). geht=True, wenn Vosk und ein Mikrofon-Backend vorhanden sind."""
    try:
        import vosk  # noqa: F401
    except Exception:
        return False, PIP_HINT
    try:
        import sounddevice  # noqa: F401
    except Exception:
        return False, PIP_HINT
    return True, ""


# --------------------------------------------------------------------------- Modell

def model_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "sprachmodell"


def have_model(data_dir: Path) -> bool:
    d = model_dir(data_dir)
    return d.is_dir() and any(d.glob("**/am/final.mdl"))


def _model_root(d: Path) -> Path:
    """Der Ordner, in dem direkt die Modelldateien liegen (Vosk-Zips haben einen Unterordner)."""
    hits = list(d.glob("**/am/final.mdl"))
    return hits[0].parent.parent if hits else d


def download_model(data_dir: Path, url: str, progress=None) -> Path:
    """Lädt das Sprachmodell herunter und entpackt es. progress(anteil_0_bis_1) optional."""
    d = model_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    zip_path = d / "modell.zip"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(zip_path, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(d)
    except (OSError, zipfile.BadZipFile, ValueError) as e:
        raise VoiceError(f"Das Sprachmodell konnte nicht geladen werden: {e}\n"
                         "Prüfe die Internetverbindung oder trage in config.json eine andere 'modell_url' ein.") from None
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass
    if not have_model(data_dir):
        raise VoiceError("Das heruntergeladene Sprachmodell ist unvollständig.")
    return _model_root(d)


# --------------------------------------------------------------------------- Erkennung

def transcribe_stream(recognizer, chunks):
    """Reicht Audio-Blöcke (PCM 16-bit mono) an einen Vosk-Recognizer weiter.

    Liefert ("partial", text) während des Sprechens und ("final", text) am Ende eines Satzes.
    Getrennt von der Mikrofon-Aufnahme, damit es sich testen lässt.
    """
    for chunk in chunks:
        if recognizer.AcceptWaveform(bytes(chunk)):
            text = json.loads(recognizer.Result()).get("text", "").strip()
            if text:
                yield ("final", text)
        else:
            partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
            if partial:
                yield ("partial", partial)
    rest = json.loads(recognizer.FinalResult()).get("text", "").strip()
    if rest:
        yield ("final", rest)


class Microphone:
    """Nimmt vom Mikrofon auf und erkennt live. on_partial/on_final werden aus einem Hintergrund-Thread
    aufgerufen (die Oberfläche muss die Updates selbst in ihren Haupt-Thread holen)."""

    def __init__(self, data_dir: Path, url: str):
        self.data_dir = Path(data_dir)
        self.url = url
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def ensure_ready(self, progress=None):
        ok, msg = available()
        if not ok:
            raise VoiceError(msg)
        if not have_model(self.data_dir):
            download_model(self.data_dir, self.url, progress)

    def start(self, on_partial, on_final, on_error=None):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(on_partial, on_final, on_error),
                                        daemon=True, name="sprache")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def listening(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self, on_partial, on_final, on_error):
        try:
            import sounddevice as sd
            import vosk
            model = vosk.Model(str(_model_root(model_dir(self.data_dir))))
            rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)
            audio: queue.Queue = queue.Queue()

            def cb(indata, frames, time_, status):
                audio.put(bytes(indata))

            def chunks():
                while not self._stop.is_set():
                    try:
                        yield audio.get(timeout=0.2)
                    except queue.Empty:
                        continue

            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000, dtype="int16",
                                   channels=1, callback=cb):
                for kind, text in transcribe_stream(rec, chunks()):
                    if self._stop.is_set():
                        break
                    (on_final if kind == "final" else on_partial)(text)
        except VoiceError as e:
            if on_error:
                on_error(str(e))
        except Exception as e:  # Mikrofon fehlt, Treiberfehler ...
            if on_error:
                on_error(f"Spracheingabe nicht möglich: {e}")
