from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from angel.config import DEFAULTS


def make_cfg(tmp: Path, **overrides) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    (tmp / "daten").mkdir(parents=True, exist_ok=True)
    (tmp / "work").mkdir(parents=True, exist_ok=True)
    cfg.update(data_dir=str(tmp / "daten"), arbeitsordner=str(tmp / "work"), modell="test",
               bestaetigung="nachfragen")  # Tests prüfen beide Modi explizit; Standard hier deterministisch
    cfg.update(overrides)
    return cfg


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="angel-test-"))
        self.work = self.tmp / "work"
        self.work.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def collect(gen):
    return list(gen)


class Approver:
    """Beantwortet Nachfragen der Reihe nach mit den vorgegebenen Antworten."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.answers.pop(0) if self.answers else "no"
