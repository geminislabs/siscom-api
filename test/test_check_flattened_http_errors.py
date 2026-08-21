"""Calibración del detector de códigos HTTP aplanados.

Un detector que nunca ha detectado nada no distingue "no hay fallos" de "no
funciona". Estos tests le dan controles positivos y negativos para que el verde
de `make all-checks` signifique algo.

Los casos positivos incluyen las tres formas de handler amplio, no solo la
obvia: `except Exception`, `except (X, Exception)` y `except:` desnudo. Las dos
últimas son puntos ciegos fáciles de dejarse al escribir el detector, porque en
el AST no son un `ast.Name` y un `getattr(tipo, "id")` devuelve `None` sin
quejarse.
"""

import textwrap

import pytest

from scripts.check_flattened_http_errors import find_flattened


def _analyze(tmp_path, source: str) -> list:
    (tmp_path / "modulo.py").write_text(textwrap.dedent(source))
    return find_flattened(tmp_path)


@pytest.mark.unit
class TestDetectsFlattening:
    """Controles positivos: el detector tiene que señalar estos."""

    def test_plain_broad_handler(self, tmp_path):
        assert _analyze(
            tmp_path,
            """
            from fastapi import HTTPException
            def f():
                try:
                    raise HTTPException(status_code=503, detail="deliberado")
                except Exception:
                    raise HTTPException(status_code=500, detail="aplanado")
            """,
        )

    def test_tuple_handler_including_exception(self, tmp_path):
        """`except (ValueError, Exception)` no es un ast.Name."""
        assert _analyze(
            tmp_path,
            """
            from fastapi import HTTPException
            def f():
                try:
                    raise HTTPException(status_code=503, detail="deliberado")
                except (ValueError, Exception):
                    raise HTTPException(status_code=500, detail="aplanado")
            """,
        )

    def test_bare_except(self, tmp_path):
        """`except:` desnudo tiene `type is None`."""
        assert _analyze(
            tmp_path,
            """
            from fastapi import HTTPException
            def f():
                try:
                    raise HTTPException(status_code=503, detail="deliberado")
                except:
                    raise HTTPException(status_code=500, detail="aplanado")
            """,
        )

    def test_raise_nested_deep_inside_the_try(self, tmp_path):
        """El `raise` casi nunca está en el primer nivel del `try`."""
        assert _analyze(
            tmp_path,
            """
            from fastapi import HTTPException
            def f():
                try:
                    for _ in range(3):
                        if True:
                            raise HTTPException(status_code=503, detail="hondo")
                except Exception:
                    raise HTTPException(status_code=500, detail="aplanado")
            """,
        )


@pytest.mark.unit
class TestDoesNotFlagCorrectCode:
    """Controles negativos: señalar estos haría el detector inservible."""

    def test_reraise_guard_before_the_broad_handler(self, tmp_path):
        """La forma correcta. El orden de los handlers importa."""
        assert not _analyze(
            tmp_path,
            """
            from fastapi import HTTPException
            def f():
                try:
                    raise HTTPException(status_code=503, detail="deliberado")
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(status_code=500, detail="imprevisto")
            """,
        )

    def test_narrow_handler_only(self, tmp_path):
        assert not _analyze(
            tmp_path,
            """
            from fastapi import HTTPException
            def f():
                try:
                    raise HTTPException(status_code=503, detail="deliberado")
                except ValueError:
                    raise HTTPException(status_code=400, detail="otra cosa")
            """,
        )

    def test_broad_handler_without_a_deliberate_raise(self, tmp_path):
        """Los 41 handlers amplios de este repo, hoy: inofensivos."""
        assert not _analyze(
            tmp_path,
            """
            def f():
                try:
                    return 1
                except Exception:
                    return 0
            """,
        )

    def test_raise_from_a_sibling_handler_is_not_caught(self, tmp_path):
        """Lanzar desde un `except` hermano no lo captura el amplio.

        Así es como el 503 de "Valkey no responde" sobrevivía en este repo. Era
        correcto, pero por la posición del `raise`, no por ninguna garantía.
        """
        assert not _analyze(
            tmp_path,
            """
            from fastapi import HTTPException
            def f():
                try:
                    return 1
                except ValueError:
                    raise HTTPException(status_code=503, detail="desde el hermano")
                except Exception:
                    raise HTTPException(status_code=500, detail="imprevisto")
            """,
        )


@pytest.mark.unit
class TestTheRepositoryItself:
    def test_app_has_no_flattened_status_codes(self):
        """Lo que comprueba `make all-checks`, fijado también aquí."""
        import pathlib

        assert find_flattened(pathlib.Path("app")) == []
