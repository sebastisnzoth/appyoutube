# NicheRadar — Fase 4: Opportunity Engine

Esta fase transforma los outliers detectados en varios competidores en oportunidades concretas.

## Incluye
- Importación de canales con YouTube Data API v3
- Sincronización de los últimos 50 videos
- Outlier Score basado en views/día vs mediana del canal
- Clustering ligero de títulos por similitud Jaccard
- Detección de subtemas repetidos
- Opportunity Cards
- Demand Score
- Cross-channel Validation Score
- Freshness Score
- Whitespace Score
- Opportunity Score compuesto
- Evidencia visible detrás de cada oportunidad
- Ideas originales a partir del tema detectado

## Opportunity Score v1
- 35% demanda
- 25% validación entre canales
- 20% frescura
- 20% espacio competitivo

## Ejecutar
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export YOUTUBE_API_KEY="TU_KEY"
python app.py
```

Abrir: http://localhost:8000

## Fase 5
- embeddings/clustering semántico real
- tendencias históricas
- briefs automáticos
- títulos, hooks y miniaturas
- Packaging Score
- pipeline editorial
- comparación entre predicción y resultado real
