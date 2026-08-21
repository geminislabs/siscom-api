#!/usr/bin/env python3
"""Detecta códigos de estado deliberados aplanados por un manejador amplio.

El patrón: un bloque `try` que lanza una `HTTPException` a conciencia, envuelto
por un `except Exception` que la vuelve a lanzar con otro código. El código
elegido arriba se pierde.

Por qué importa más de lo que parece: el código de estado es lo que decide qué
hace el cliente. Un 503 aplanado a 500 le dice "esto está roto" en vez de
"reintenta". Un 503 aplanado a 401 le dice "tu credencial es mala", así que la
reemite — contra el servicio que está caído. Este repo tuvo el primer caso en
`/public/share-location/init`: un corte de base de datos devolvía 503 desde
dentro y 500 desde fuera.

`grep` no sirve para buscarlo: hay `except Exception` por todas partes y casi
todos son inofensivos. Lo que importa es la conjunción, y eso pide recorrer el
árbol sintáctico.

**Por qué esto es una comprobación continua y no una auditoría puntual.** Un
conteo sobre `app/` da 41 manejadores amplios, de los cuales **ninguno**
re-lanza tal cual: todos capturan y transforman. Que hoy salga verde no es una
propiedad del código, es que todavía no coincide ninguno con un `raise`
deliberado. Dicho de otro modo: hay 41 sitios donde añadir un
`raise HTTPException(503, ...)` produce un 500 en silencio. El valor de este
script está en que corra cuando alguien añada el próximo, no en el verde de
hoy — que es exactamente como apareció el caso de `/public/share-location/init`
en este repo: al añadir el 503, no antes.

El detector está calibrado con controles positivos y negativos en
`test/test_check_flattened_http_errors.py`, incluidas las formas `except (X,
Exception)` y `except:` desnudo, que son puntos ciegos fáciles de dejarse.

La forma correcta es interceptar antes:

    except HTTPException:
        raise            # los códigos deliberados pasan tal cual
    except Exception:
        ...              # el manejador amplio, solo para lo imprevisto

Uso:

    python scripts/check_flattened_http_errors.py [directorio]

Sale con código 1 si encuentra algo, para poder engancharlo a CI.

Idea del método: la sesión de siscom-admin-api, que encontró el mismo patrón
en su repo por la misma vía.
"""

import ast
import pathlib
import sys

_BROAD = {"Exception", "BaseException"}


def _deliberate_raise_line(body: list[ast.stmt]) -> int | None:
    """Línea del primer `raise HTTPException(...)` del cuerpo, si lo hay."""
    for node in body:
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Raise) or sub.exc is None:
                continue
            if not isinstance(sub.exc, ast.Call):
                continue
            func = sub.exc.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "HTTPException":
                return sub.lineno
    return None


def _handler_names(handler: ast.ExceptHandler) -> list[str]:
    if handler.type is None:
        return ["<bare except>"]
    if isinstance(handler.type, ast.Name):
        return [handler.type.id]
    if isinstance(handler.type, ast.Tuple):
        return [getattr(e, "id", "") for e in handler.type.elts]
    return []


def find_flattened(root: pathlib.Path) -> list[tuple[pathlib.Path, int, int, str]]:
    findings = []

    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), str(path))
        except SyntaxError as e:
            print(f"⚠️  No se pudo analizar {path}: {e}", file=sys.stderr)
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue

            raise_line = _deliberate_raise_line(node.body)
            if raise_line is None:
                continue

            for handler in node.handlers:
                names = _handler_names(handler)
                # Un manejador de HTTPException antes del amplio ya protege:
                # el orden importa, así que se para en el primero que aplique.
                if "HTTPException" in names:
                    break
                if names and (set(names) & _BROAD or names == ["<bare except>"]):
                    findings.append((path, node.lineno, raise_line, names[0]))
                    break

    return findings


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "app")
    findings = find_flattened(root)

    if not findings:
        print(f"✅ {root}: ningún código deliberado queda aplanado.")
        return 0

    print(f"❌ {len(findings)} caso(s) donde un manejador amplio aplana el código:\n")
    for path, try_line, raise_line, handler in findings:
        print(f"  {path}:{try_line}")
        print(f"      raise HTTPException en la línea {raise_line}")
        print(f"      lo captura `except {handler}` del mismo try")
        print("      → añade `except HTTPException: raise` antes\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
