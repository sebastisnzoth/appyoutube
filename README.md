# NicheRadar — MVP Global Discovery

NicheRadar descubre automáticamente canales con señales de aceleración y, a partir de ellos, detecta nichos y micro-nichos emergentes.

## Estado actual

Esta versión implementa los primeros cuatro pasos del roadmap técnico:

1. motor de descubrimiento más amplio que `mostPopular`;
2. `Growth Opportunity Score v2`;
3. historial persistente de ejecuciones del radar;
4. `Opportunity Timeline` con ventanas 24h / 7d / 30d.

## 1. Motor de descubrimiento

El modo `balanced` combina dos fuentes públicas de YouTube Data API:

- `videos.list(chart=mostPopular)`;
- `search.list` sobre videos publicados en los últimos 30 días, ordenados por views y filtrados por categoría/región.

Esto permite encontrar candidatos que todavía no dominan `mostPopular`, incluyendo canales pequeños y medianos con videos recientes ganando tracción.

Hay tres modos:

- `light`: solo `mostPopular`, menor uso de cuota;
- `balanced`: popular + búsqueda reciente, recomendado;
- `deep`: reservado para ampliar búsqueda en iteraciones posteriores.

## 2. Growth Opportunity Score v2

El score es de 0 a 100 y separa cinco componentes visibles:

- **30% Momentum**: velocidad reciente y, cuando existe histórico, crecimiento observado entre snapshots;
- **25% Outliers**: densidad y fuerza de videos 2x+;
- **20% Audience Efficiency**: views recientes en relación con suscriptores;
- **15% Freshness**: recencia de señales fuertes + antigüedad del canal;
- **10% Consistency**: repetición de señales en varios videos y actividad reciente.

## 3. Historial del radar

Cada ejecución guarda datos en:

- `radar_runs`;
- `radar_run_channels`;
- `channel_snapshots`.

Endpoints:

```text
GET /api/discovery/history
GET /api/discovery/history/<run_id>
GET /api/channels/<channel_id>/history
```

## 4. Opportunity Timeline

Cada canal puede mostrar crecimiento observado en tres ventanas:

- **24h**
- **7d**
- **30d**

Para cada ventana se calcula, cuando existe cobertura histórica suficiente:

- delta de views;
- delta de suscriptores;
- views por día;
- suscriptores por día;
- crecimiento porcentual.

El backend busca un snapshot anterior a cada ventana y lo compara con el snapshot actual. Si todavía no existe un snapshot suficientemente antiguo, devuelve `available: false` en vez de inventar una cifra.

Endpoint nuevo:

```text
GET /api/channels/<channel_id>/timeline
```

La respuesta también incluye una clasificación de tendencia:

- `Recolectando datos`
- `Emergente`
- `Acelerando`
- `Fuerte`
- `Desacelerando`
- `Observando`

La clasificación usa las velocidades observadas en 24h, 7d y 30d, junto con el Growth Opportunity Score cuando corresponde.

Ejemplo conceptual:

```json
{
  "windows": {
    "24h": {"available": true, "views_delta": 12000, "views_per_day": 11850},
    "7d": {"available": true, "views_delta": 52000, "views_per_day": 7600},
    "30d": {"available": false}
  },
  "trend": {
    "status": "Acelerando",
    "direction": "up"
  }
}
```

## Radar principal

```text
POST /api/discovery/run
```

Ejemplo:

```json
{
  "region": "US",
  "category_limit": 8,
  "channels_limit": 20,
  "discovery_mode": "balanced"
}
```

Cada canal devuelto por el radar incluye ahora `timeline`, además del Growth Opportunity Score v2.

## Ejecutar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export YOUTUBE_API_KEY="TU_KEY"
python app.py
```

Abrir:

```text
http://localhost:8000
```

Nunca subas la API key real al repositorio.

## Roadmap técnico siguiente

5. Confidence Score por canal y oportunidad.
6. Google OAuth + My Channel + publicación a YouTube.
7. Financial Dashboard con YouTube Analytics para canales autorizados.

## Importante

Los scores y estados de tendencia de NicheRadar son heurísticas experimentales del producto. Las métricas públicas de canales externos provienen de YouTube Data API. La primera ejecución no puede producir una ventana histórica de 24h, 7d o 30d si esos snapshots todavía no existen; la aplicación lo indica explícitamente.
