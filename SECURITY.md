# Política de seguridad

## Versiones soportadas

| Versión         | Soportada | Notas                             |
| --------------- | --------- | --------------------------------- |
| Último tag `v*` | Sí        | Releases desplegados a producción |
| `develop`       | Sí        | Rama de integración activa        |
| Otras ramas     | No        | Sin garantía de parches           |

## Reportar una vulnerabilidad

**No reportes vulnerabilidades de seguridad mediante issues públicos de GitHub.**

Envía un reporte privado a:

**[security@geminislabs.com](mailto:security@geminislabs.com)**

Si el buzón no está disponible, usa **[contacto@geminislabs.com](mailto:contacto@geminislabs.com)** con asunto `SECURITY: siscom-api`.

Incluye:

1. Descripción del problema y el impacto potencial
2. Pasos para reproducir (o prueba de concepto)
3. Versión o commit afectado
4. Tu contacto para seguimiento

## Buenas prácticas del proyecto

- **Nunca** commitees secretos JWT, PASETO, Kafka ni credenciales de DB
- Rotar claves si estuvieron expuestas en git, aunque ya no estén en el código actual
- `make scan-secrets` — Gitleaks en el árbol de trabajo
- Dependencias: `make audit-deps` (pip-audit) y `make scan-osv` en CI

## Dependencias

Vulnerabilidades en dependencias Python se auditan con `pip-audit` y OSV-Scanner en el job `security` de CI.
