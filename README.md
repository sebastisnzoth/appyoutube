# NicheRadar — MVP Global Discovery

NicheRadar descubre automáticamente canales con señales de aceleración, detecta micro-nichos emergentes y ahora permite conectar el canal propio mediante Google OAuth para publicar videos directamente en YouTube.

## Estado actual

La versión actual implementa:

1. motor de descubrimiento más amplio que `mostPopular`;
2. `Growth Opportunity Score v2`;
3. historial persistente del radar;
4. `Opportunity Timeline` 24h / 7d / 30d;
5. `Confidence Score v1` para canales y micro-nichos;
6. Google OAuth + My Channel + publicación directa a YouTube.

## Scores del radar

`Growth Opportunity Score` mide potencial. `Confidence Score` mide qué tan sólida es la evidencia. El Timeline usa snapshots reales y nunca inventa una ventana histórica si todavía no existe cobertura suficiente.

## Google OAuth + My Channel

El flujo OAuth usa una aplicación web de Google y solicita únicamente estos scopes de YouTube:

```text
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/youtube.upload
```

El primero permite leer el canal autorizado. El segundo permite gestionar/subir videos. Para una aplicación pública, Google puede exigir verificación OAuth antes de eliminar la pantalla de aplicación no verificada.

Las credenciales OAuth se guardan cifradas en SQLite usando Fernet. La clave de cifrado **nunca** debe subirse al repositorio.

### Variables requeridas

```bash
YOUTUBE_API_KEY=...
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/auth/youtube/callback
FLASK_SECRET_KEY=...
TOKEN_ENCRYPTION_KEY=...
```

Generar una clave Fernet:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

En Google Cloud Console registra exactamente el mismo redirect URI que uses en `GOOGLE_OAUTH_REDIRECT_URI`.

## Publicación a YouTube

La UI de `Mi canal` permite:

- conectar/desconectar un canal;
- refrescar métricas públicas del canal autenticado;
- seleccionar título;
- descripción;
- tags;
- categoría;
- privacidad `private`, `unlisted` o `public`;
- archivo de video;
- miniatura opcional;
- subir mediante `videos.insert` y `thumbnails.set`.

El valor predeterminado de privacidad es **private** para evitar publicaciones accidentales.

Las subidas quedan registradas localmente en `published_videos`.

## Endpoints OAuth y publicación

```text
GET  /auth/youtube/start
GET  /auth/youtube/callback
GET  /api/me/youtube
GET  /api/me/youtube/refresh
POST /api/me/youtube/disconnect
POST /api/publish/youtube
GET  /api/publish/history
```

El endpoint `POST /api/publish/youtube` recibe `multipart/form-data`.

## Endpoints del radar

```text
POST /api/discovery/run
GET  /api/discovery/history
GET  /api/discovery/history/<run_id>
GET  /api/channels/<channel_id>/history
GET  /api/channels/<channel_id>/timeline
POST /api/channels
GET  /api/channels
```

## Ejecutar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export YOUTUBE_API_KEY="TU_KEY"
export GOOGLE_OAUTH_CLIENT_ID="..."
export GOOGLE_OAUTH_CLIENT_SECRET="..."
export GOOGLE_OAUTH_REDIRECT_URI="http://localhost:8000/auth/youtube/callback"
export FLASK_SECRET_KEY="..."
export TOKEN_ENCRYPTION_KEY="..."
python oauth_app.py
```

Abrir:

```text
http://localhost:8000
```

Para Gunicorn:

```bash
gunicorn oauth_app:app
```

## Seguridad y producción

- El repositorio es público: nunca guardes API keys, OAuth client secrets, refresh tokens ni `TOKEN_ENCRYPTION_KEY` en Git.
- Para producción usa HTTPS y una `FLASK_SECRET_KEY` larga y aleatoria.
- El upload actual pasa por el servidor Flask. Es apropiado para el MVP; para archivos grandes y escala real conviene evolucionarlo a una arquitectura de uploads/resumable jobs que no mantenga una petición HTTP abierta durante toda la subida.
- Los permisos OAuth deben pedirse con el menor alcance posible.

## Roadmap siguiente

7. Financial Dashboard con YouTube Analytics para canales autorizados.

Ese paso añadirá scopes de Analytics únicamente cuando se implemente el módulo financiero, en lugar de pedirlos antes de que sean necesarios.

## Importante

Los scores de NicheRadar son heurísticas experimentales del producto. Las métricas públicas observadas provienen de YouTube Data API. Los datos privados del propietario solo deben mostrarse cuando provienen de una autorización OAuth válida.
