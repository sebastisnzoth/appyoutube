# NicheRadar — MVP Global Discovery

NicheRadar descubre automáticamente canales con señales de aceleración y, a partir de ellos, detecta nichos y micro-nichos emergentes.

## Estado actual

Esta rama implementa los primeros tres pasos del roadmap técnico:

1. motor de descubrimiento más amplio que `mostPopular`;
2. `Growth Opportunity Score v2`;
3. historial persistente de ejecuciones del radar.

## 1. Motor de descubrimiento

El modo `balanced` combina dos fuentes públicas de YouTube Data API:

- `videos.list(chart=mostPopular)`;
- `search.list` sobre videos publicados en los últimos 30 días, ordenados por views y filtrados por categoría/región.

Esto permite encontrar candidatos que todavía no dominan `mostPopular`, incluyendo canales pequeños y medianos con videos recientes ganando tracción.

Hay tres modos:

- `light`: solo `mostPopular`, menor uso de cuota;
- `balanced`: popular + búsqueda reciente, recomendado;
- `deep`: reservado para ampliar búsqueda en iteraciones posteriores.

> `search.list` consume bastante más cuota de YouTube API que `videos.list`, por eso el modo light sigue disponible.

## 2. Growth Opportunity Score v2

El score es de 0 a 100 y separa cinco componentes visibles:

- **30% Momentum**: velocidad reciente y, cuando existe histórico, crecimiento observado entre snapshots;
- **25% Outliers**: densidad y fuerza de videos 2x+;
- **20% Audience Efficiency**: views recientes en relación con suscriptores;
- **15% Freshness**: recencia de las señales fuertes + antigüedad del canal;
- **10% Consistency**: repetición de señales en varios videos y actividad reciente.

El primer escaneo usa proxies actuales. Desde el segundo escaneo del mismo canal, Momentum puede incorporar crecimiento observado real entre snapshots.

## 3. Historial del radar

Cada ejecución crea un registro en:

- `radar_runs`;
- `radar_run_channels`;
- `channel_snapshots`.

Esto prepara la base de datos para el siguiente paso: **Opportunity Timeline 24h / 7d / 30d**.

Endpoints nuevos:

```text
GET /api/discovery/history
GET /api/discovery/history/<run_id>
GET /api/channels/<channel_id>/history
```

El endpoint principal sigue siendo:

```text
POST /api/discovery/run
```

Ejemplo de body:

```json
{
  "region": "US",
  "category_limit": 6,
  "channels_limit": 20,
  "discovery_mode": "balanced"
}
```

## Micro-nichos

Los videos señal se siguen agrupando con tokenización + similitud Jaccard. El clustering es intencionalmente simple y auditable para el MVP. Después puede reemplazarse por embeddings semánticos.

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

4. Opportunity Timeline con ventanas 24h / 7d / 30d.
5. Confidence Score por canal y oportunidad.
6. Google OAuth + My Channel + publicación a YouTube.
7. Financial Dashboard con YouTube Analytics para canales autorizados.

## Importante

Los scores de NicheRadar son heurísticas experimentales del producto. Las métricas públicas de canales externos provienen de YouTube Data API; crecimiento, oportunidad y monetización potencial deben mostrarse como cálculos/estimaciones cuando no provienen directamente de datos privados autorizados del propietario del canal.
