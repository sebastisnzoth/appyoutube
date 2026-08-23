# NicheRadar — MVP Global Discovery

NicheRadar ya no parte de un nicho fijo ni de una lista manual de competidores. El núcleo del MVP es descubrir automáticamente canales que muestran señales de crecimiento acelerado y, a partir de esos canales, detectar nichos y micro-nichos emergentes.

## Objetivo del MVP

Responder estas preguntas:

1. ¿Qué canales están despegando ahora?
2. ¿Qué nicho o micro-nicho comparten?
3. ¿Qué tan fuerte es la oportunidad?
4. ¿Qué evidencia valida esa señal?

## Flujo principal

1. Elegir una región.
2. Ejecutar `Radar global`.
3. NicheRadar recorre categorías de YouTube.
4. Detecta canales candidatos desde videos populares actuales.
5. Importa datos públicos de cada canal.
6. Analiza videos recientes.
7. Calcula views/día, views/sub, actividad, antigüedad y outliers.
8. Calcula un `Channel Opportunity Score`.
9. Agrupa videos señal en nichos/micro-nichos mediante similitud de títulos.
10. Calcula un `Opportunity Score` por micro-nicho y muestra evidencia.

## Channel Opportunity Score v1

El score combina:

- 30% velocidad de views de los videos recientes
- 20% views por suscriptor
- 20% densidad de videos outlier
- 15% frescura / antigüedad del canal
- 15% actividad reciente

Cuando ya existe un snapshot anterior del mismo canal, el sistema incorpora también crecimiento observado entre escaneos.

## Detección de outliers

Para cada canal se analizan videos recientes y se calcula:

`Outlier Score = views/día del video / mediana de views/día del canal`

Los videos por encima de 1.5x se usan como señales para formar clusters temáticos. Los videos 2x+ cuentan como outliers fuertes del canal.

## Opportunity Score de nicho v1

Los clusters temáticos reciben un score basado en:

- demanda observada
- validación entre varios canales
- frescura de los videos señal
- calidad media de los canales que validan el tema

El clustering actual usa tokenización + similitud Jaccard. Es simple, auditable y suficiente para el MVP, pero después puede reemplazarse por embeddings semánticos.

## Limitación importante

El primer escaneo no puede medir crecimiento histórico real porque todavía no existe una medición anterior. Por eso usa proxies actuales: views/día, outliers, views/sub, actividad y antigüedad. Los siguientes escaneos guardan snapshots y permiten empezar a medir crecimiento observado entre fechas.

## Ejecutar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export YOUTUBE_API_KEY="TU_KEY"
python app.py
```

Abrir:

`http://localhost:8000`

## Variable de entorno

No subas la API key al repositorio.

```bash
YOUTUBE_API_KEY=tu_clave_real
```

## Próximos pasos

- guardar ejecuciones del radar como historial
- comparar crecimiento a 24h / 7d / 30d
- usar embeddings para micro-nichos semánticos
- ampliar el universo de descubrimiento más allá de `mostPopular`
- filtros por idioma, país, edad y tamaño de canal
- detección de canales nuevos con alta velocidad aunque todavía no sean populares
- alertas cuando aparezca una oportunidad nueva
- ideas de contenido derivadas de la evidencia
