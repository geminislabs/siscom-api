#!/usr/bin/env python3
"""Huella de la clave efectiva de compartir ubicación, sin revelar el secreto.

Sirve para responder una pregunta concreta: **¿siscom-api y siscom-admin-api
tienen la misma clave en producción?** Si no la tienen, compartir ubicación
está roto —uno firma con una clave y el otro valida con otra— y ninguno de los
dos servicios puede saberlo por su cuenta.

Por qué una huella y no el valor: una huella contesta la pregunta sin sacar el
secreto de la máquina, así que se puede pegar en un chat o en un ticket.

**Importante — compara el material EFECTIVO, no la cadena de configuración.**
Los dos servicios derivan distinto a partir del mismo texto: admin-api rellena
con ceros hasta 32 bytes (`ljust(32, b"\\0")`) y siscom-api no. Con la misma
cadena de entrada saldrían huellas distintas, así que comparar
`sha256(b64decode(valor))` no contesta nada. Lo que hay que comparar son los
bytes que cada lado pasa a `Key.new`, que es lo que imprime este script.

Uso, dentro del contenedor de cada servicio:

    docker exec -it siscom-api python scripts/share_key_fingerprint.py

Y el equivalente en admin-api. Si las huellas coinciden, las claves son la
misma. Si no, no lo son.

Alternativa sin ejecutar nada: generar un enlace de compartir en producción
desde admin-api y abrirlo. Si funciona, las claves coinciden.
"""

import base64
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402

_V4_LOCAL_KEY_BYTES = 32


def fingerprint(label: str, configured: str) -> None:
    if not configured:
        print(f"{label:<26} (sin configurar)")
        return

    try:
        # Decodificación laxa a propósito: es la que aplica el validador a la
        # clave heredada, así que es el material realmente en uso.
        effective = base64.b64decode(configured)
    except Exception as e:
        print(f"{label:<26} ERROR: no decodifica ({e})")
        return

    digest = hashlib.sha256(effective).hexdigest()[:12]
    print(f"{label:<26} {digest}")

    try:
        base64.b64decode(configured, validate=True)
    except Exception:
        print(
            "    ⚠️  No es base64 estricto: el decodificador laxo está "
            "descartando caracteres en silencio."
        )

    if len(effective) != _V4_LOCAL_KEY_BYTES:
        print(
            f"    ⚠️  {len(effective)} bytes; PASETO v4.local requiere "
            f"{_V4_LOCAL_KEY_BYTES}."
        )
        # Este es el punto importante y el menos evidente: no es solo una
        # cuestión de entropía. admin-api rellena con ceros hasta 32 bytes y
        # siscom-api no, así que una clave que no mida ya 32 produce material
        # EFECTIVO distinto en cada servicio aunque la cadena configurada sea
        # idéntica. Los tokens no validan entre servicios, y la comparación de
        # huellas dará distinto sin que nadie haya configurado nada mal.
        print("    🚨 admin-api rellena con ceros hasta 32 bytes y siscom-api no.")
        print("       Con una clave que no mida 32, las claves EFECTIVAS divergen")
        print("       aunque las dos variables tengan el mismo contenido:")
        print("       compartir ubicación NO valida entre servicios.")

    if effective and not any(effective):
        print("    🚨 CLAVE DE CEROS: es el marcador del .env.example, es PÚBLICA.")


def main() -> None:
    print("Huella del material de clave efectivo (sha256 de los bytes reales)\n")
    fingerprint("SHARE_LOCATION_KEY_B64", settings.SHARE_LOCATION_KEY_B64)
    fingerprint("PASETO_SECRET_KEY", settings.PASETO_SECRET_KEY)
    print(
        "\nCompara cada huella con la del mismo nombre en admin-api.\n"
        "\n  Iguales   → misma cadena Y misma derivación: el flujo funciona.\n"
        "  Distintas → NO deduzcas que las variables tienen contenido distinto.\n"
        "              Si arriba hay aviso de longitud, basta el relleno para\n"
        "              explicarlo. Los avisos dicen cuál de las dos cosas falló."
    )


if __name__ == "__main__":
    main()
