# NicheRadar — MVP Global Discovery

NicheRadar descubre automáticamente canales con señales de aceleración y detecta nichos y micro-nichos emergentes con evidencia visible.

## Estado actual

La versión actual implementa:

1. motor de descubrimiento más amplio que `mostPopular`;
2. `Growth Opportunity Score v2`;
3. historial persistente del radar;
4. `Opportunity Timeline` 24h / 7d / 30d;
5. `Confidence Score v1` para canales y micro-nichos.

## Growth Opportunity Score v2

- 30% Momentum
- 25% Outliers
- 20% Audience Efficiency
- 15% Freshness
- 10% Consistency

Este score mide **potencial**.

## Confidence Score v1

Confidence mide **qué tan sólida es la evidencia detrás del score**, no el potencial.

### Canal

El Confidence Score de canal usa cuatro factores:

- tamaño de la muestra de videos;
- repetición de outliers;
- profundidad del historial de snapshots;
- cobertura disponible de Timeline 24h / 7d / 30d.

La salida incluye:

```json
{
  "confidence": {
    "score": 78.4,
    "label": "Alta",
    "factors": {
      "video_sample": 100,
      "outlier_repeatability": 75,
      "snapshot_depth": 62.5,
      "timeline_coverage": 66.7
    }
  }
}
```

### Micro-nicho

El Confidence Score de oportunidad usa:

- cantidad de canales independientes;
- cantidad de videos señal;
- fuerza media de los outliers;
- frescura de las señales.

Esto evita tratar igual una oportunidad basada en dos videos que otra validada por muchos canales y múltiples señales.

Etiquetas:

- `Alta`: 75–100
- `Media`: 50–74.9
- `Baja`: 0–49.9

## Opportunity Timeline

Cada canal puede incluir ventanas observadas:

- 24h
- 7d
- 30d

Si no existe suficiente historial, la API devuelve `available: false` y no inventa datos.

## Endpoints principales

```text
POST /api/discovery/run
GET /api/discovery/history
GET /api/discovery/history/<run_id>
GET /api/channels/<channel_id>/history
GET /api/channels/<channel_id>/timeline
POST /api/channels
GET /api/channels
```

Las ejecuciones guardan también `confidence_score` y `confidence_label` por canal en `radar_run_channels`.

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

## Roadmap siguiente

6. Google OAuth + My Channel + publicación a YouTube.
7. Financial Dashboard con YouTube Analytics para canales autorizados.

## Importante

Los scores de NicheRadar son heurísticas experimentales. `Growth Opportunity Score` y `Confidence Score` deben mostrarse como cálculos del producto. Las métricas públicas observadas provienen de YouTube Data API; datos privados del propietario requerirán OAuth y APIs autorizadas de YouTube.
