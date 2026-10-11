"""`_version_del_repo`: la versión que anuncia el servicio sale del fichero
VERSION, y si no está se declara `unknown` en vez de inventarse un número."""

from pathlib import Path

import pytest

from app.core import config


@pytest.mark.unit
class TestVersionDelRepo:
    def test_lee_el_fichero_version(self):
        esperado = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
        assert config._version_del_repo() == esperado

    def test_sin_fichero_es_unknown(self, monkeypatch):
        def falla(*_args, **_kwargs):
            raise FileNotFoundError("VERSION")

        monkeypatch.setattr(Path, "read_text", falla)
        assert config._version_del_repo() == "unknown"

    def test_fichero_vacio_es_unknown(self, monkeypatch):
        monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: "  \n")
        assert config._version_del_repo() == "unknown"
