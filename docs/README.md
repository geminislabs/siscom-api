# 📚 Documentación de SISCOM API

Documentación técnica completa de la API REST para gestión de comunicaciones GPS.

## 📖 Documentos Disponibles

| Documento                                     | Descripción                                                                              |
| --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **[📘 API REST Guide](API_REST_GUIDE.md)**    | Documentación completa de todos los endpoints, parámetros, respuestas y códigos de error |
| **[📮 Ejemplos de Uso](POSTMAN_EXAMPLES.md)** | Ejemplos prácticos con cURL, Postman, JavaScript, Python y Svelte                        |
| **[📊 Métricas](METRICS.md)**                 | Sistema de métricas StatsD/Telegraf/InfluxDB, configuración y troubleshooting            |
| **[📈 Queries Grafana](GRAFANA_QUERIES.md)**  | Dashboards, queries de Flux, alertas y visualizaciones                                   |
| **[🚀 Deployment](DEPLOYMENT.md)**            | Guía de despliegue en EC2 con GitHub Actions y configuración de variables                |
| **[🔌 WebSocket Streaming](WEBSOCKET_STREAMING.md)** | Contrato WebSocket de posiciones + alertas por `device_id`                           |
| **[🔐 Data token: unidades compartidas](DATA_TOKEN_SHARED_UNITS.md)** | Contrato v1.4: acceso solo telemetría y revocación inmediata          |

## 🎯 Guía Rápida

### Para Desarrolladores

**Quiero usar el API:**

1. Lee [API_REST_GUIDE.md](API_REST_GUIDE.md) para entender los endpoints
2. Revisa [POSTMAN_EXAMPLES.md](POSTMAN_EXAMPLES.md) para ver ejemplos en tu lenguaje
3. Usa [Swagger UI](http://localhost:8000/api/docs) para probar interactivamente

**Quiero integrar en mi aplicación:**

1. Copia ejemplos de [POSTMAN_EXAMPLES.md](POSTMAN_EXAMPLES.md)
2. Adapta según tu framework (JavaScript, Python, Svelte, etc.)
3. Consulta [API_REST_GUIDE.md](API_REST_GUIDE.md) para detalles específicos

### Para DevOps

**Quiero deployar:**

1. Sigue [DEPLOYMENT.md](DEPLOYMENT.md) paso a paso
2. Configura las variables de GitHub indicadas
3. Haz push a master para auto-deploy

**Quiero configurar métricas:**

1. Lee [METRICS.md](METRICS.md) para entender el sistema
2. Configura Telegraf según las instrucciones
3. Usa [GRAFANA_QUERIES.md](GRAFANA_QUERIES.md) para crear dashboards

## 📊 Métricas Disponibles

El sistema recolecta automáticamente:

- **`siscom_api.requests`** (counter) - Peticiones totales, ideal para calcular rate
- **`siscom_api.latency.stream`** (timer) - Latencia del endpoint /stream con percentiles
- **`siscom_api.sse.active_connections`** (gauge) - Conexiones SSE activas en tiempo real

Ver [METRICS.md](METRICS.md) para configuración completa y [GRAFANA_QUERIES.md](GRAFANA_QUERIES.md) para visualización.

## 🔗 Enlaces Útiles

- **Swagger UI**: <http://localhost:8000/api/docs> (documentación interactiva)
- **ReDoc**: <http://localhost:8000/api/redoc> (documentación alternativa)
- **Health Check**: <http://localhost:8000/health>
- **README Principal**: [../README.md](../README.md)

## 🆘 Ayuda

- **Para usar el API**: [API_REST_GUIDE.md](API_REST_GUIDE.md)
- **Para ver ejemplos**: [POSTMAN_EXAMPLES.md](POSTMAN_EXAMPLES.md)
- **Para deployar**: [DEPLOYMENT.md](DEPLOYMENT.md)
- **Para métricas**: [METRICS.md](METRICS.md)
- **Para dashboards**: [GRAFANA_QUERIES.md](GRAFANA_QUERIES.md)

---

**Volver a**: [README Principal](../README.md)
