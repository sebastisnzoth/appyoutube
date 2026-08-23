import json
import os
import tempfile
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from flask import jsonify, redirect, request, session, url_for
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app import app, db, init_db

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]

app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024)))


def oauth_ready():
    return bool(
        os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        and os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
        and os.getenv("TOKEN_ENCRYPTION_KEY")
        and os.getenv("FLASK_SECRET_KEY")
    )


def oauth_client_config():
    return {
        "web": {
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def redirect_uri():
    explicit = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    return request.url_root.rstrip("/") + url_for("youtube_oauth_callback")


def cipher():
    key = os.getenv("TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("Falta TOKEN_ENCRYPTION_KEY")
    return Fernet(key.encode())


def init_oauth_db():
    init_db()
    conn = db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS youtube_accounts(
            channel_id TEXT PRIMARY KEY,
            title TEXT,
            thumbnail TEXT,
            credentials_enc TEXT NOT NULL,
            scopes TEXT,
            connected_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS published_videos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_video_id TEXT,
            channel_id TEXT,
            title TEXT,
            privacy_status TEXT,
            created_at TEXT,
            youtube_url TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def credentials_to_encrypted(credentials):
    payload = credentials.to_json().encode()
    return cipher().encrypt(payload).decode()


def credentials_from_encrypted(value):
    try:
        payload = cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("No se pudieron descifrar las credenciales OAuth") from exc
    return Credentials.from_authorized_user_info(json.loads(payload), scopes=SCOPES)


def save_account(channel, credentials):
    snippet = channel.get("snippet", {})
    thumbs = snippet.get("thumbnails", {})
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    conn.execute(
        """
        INSERT INTO youtube_accounts(channel_id,title,thumbnail,credentials_enc,scopes,connected_at,updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(channel_id) DO UPDATE SET
            title=excluded.title,
            thumbnail=excluded.thumbnail,
            credentials_enc=excluded.credentials_enc,
            scopes=excluded.scopes,
            updated_at=excluded.updated_at
        """,
        (
            channel["id"],
            snippet.get("title", ""),
            thumb,
            credentials_to_encrypted(credentials),
            " ".join(credentials.scopes or SCOPES),
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def load_account(channel_id=None):
    conn = db()
    if channel_id:
        row = conn.execute("SELECT * FROM youtube_accounts WHERE channel_id=?", (channel_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM youtube_accounts ORDER BY updated_at DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def save_refreshed_credentials(channel_id, credentials):
    conn = db()
    conn.execute(
        "UPDATE youtube_accounts SET credentials_enc=?,updated_at=? WHERE channel_id=?",
        (credentials_to_encrypted(credentials), datetime.now(timezone.utc).isoformat(), channel_id),
    )
    conn.commit()
    conn.close()


def authorized_youtube(channel_id=None):
    account = load_account(channel_id)
    if not account:
        raise RuntimeError("No hay un canal de YouTube conectado")
    credentials = credentials_from_encrypted(account["credentials_enc"])
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleRequest())
        save_refreshed_credentials(account["channel_id"], credentials)
    if not credentials.valid:
        raise RuntimeError("La autorización de YouTube expiró. Vuelve a conectar el canal.")
    return account, credentials, build("youtube", "v3", credentials=credentials, cache_discovery=False)


@app.get("/auth/youtube/start")
def youtube_oauth_start():
    if not oauth_ready():
        return jsonify({"error": "Configura GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, FLASK_SECRET_KEY y TOKEN_ENCRYPTION_KEY."}), 503
    flow = Flow.from_client_config(oauth_client_config(), scopes=SCOPES)
    flow.redirect_uri = redirect_uri()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["youtube_oauth_state"] = state
    return redirect(authorization_url)


@app.get("/auth/youtube/callback")
def youtube_oauth_callback():
    if not oauth_ready():
        return redirect("/?oauth=not_configured")
    expected_state = session.pop("youtube_oauth_state", None)
    returned_state = request.args.get("state")
    if not expected_state or expected_state != returned_state:
        return redirect("/?oauth=state_error")

    flow = Flow.from_client_config(oauth_client_config(), scopes=SCOPES, state=expected_state)
    flow.redirect_uri = redirect_uri()
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    channels = response.get("items", [])
    if not channels:
        return redirect("/?oauth=no_channel")
    for channel in channels:
        save_account(channel, credentials)
    return redirect("/?oauth=connected")


@app.get("/api/me/youtube")
def my_youtube_channel():
    conn = db()
    rows = [dict(r) for r in conn.execute(
        "SELECT channel_id,title,thumbnail,scopes,connected_at,updated_at FROM youtube_accounts ORDER BY updated_at DESC"
    )]
    conn.close()
    return jsonify({"oauth_configured": oauth_ready(), "connected": bool(rows), "accounts": rows})


@app.post("/api/me/youtube/disconnect")
def disconnect_youtube():
    body = request.get_json(silent=True) or {}
    channel_id = body.get("channel_id")
    conn = db()
    if channel_id:
        conn.execute("DELETE FROM youtube_accounts WHERE channel_id=?", (channel_id,))
    else:
        conn.execute("DELETE FROM youtube_accounts")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/me/youtube/refresh")
def refresh_my_youtube():
    try:
        account, credentials, youtube = authorized_youtube(request.args.get("channel_id"))
        response = youtube.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
        items = response.get("items", [])
        current = next((item for item in items if item.get("id") == account["channel_id"]), items[0] if items else None)
        if not current:
            return jsonify({"error": "No se encontró el canal autorizado"}), 404
        save_account(current, credentials)
        stats = current.get("statistics", {})
        return jsonify({
            "channel_id": current["id"],
            "title": current.get("snippet", {}).get("title", ""),
            "subscribers": int(stats.get("subscriberCount", 0) or 0),
            "views": int(stats.get("viewCount", 0) or 0),
            "videos": int(stats.get("videoCount", 0) or 0),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/publish/youtube")
def publish_to_youtube():
    if "video" not in request.files:
        return jsonify({"error": "Falta el archivo de video"}), 400
    video_file = request.files["video"]
    if not video_file.filename:
        return jsonify({"error": "Falta el archivo de video"}), 400

    title = request.form.get("title", "").strip()
    if not title:
        return jsonify({"error": "Falta el título"}), 400
    description = request.form.get("description", "").strip()
    privacy = request.form.get("privacy_status", "private").strip()
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"
    tags = [tag.strip() for tag in request.form.get("tags", "").split(",") if tag.strip()][:30]
    category_id = request.form.get("category_id", "22").strip() or "22"
    channel_id = request.form.get("channel_id", "").strip() or None

    video_path = None
    thumbnail_path = None
    try:
        account, _, youtube = authorized_youtube(channel_id)
        suffix = os.path.splitext(video_file.filename)[1] or ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_video:
            video_file.save(temp_video.name)
            video_path = temp_video.name

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {"privacyStatus": privacy},
        }
        media = MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True)
        insert = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = insert.next_chunk()

        video_id = response["id"]
        thumbnail = request.files.get("thumbnail")
        if thumbnail and thumbnail.filename:
            thumb_suffix = os.path.splitext(thumbnail.filename)[1] or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=thumb_suffix) as temp_thumb:
                thumbnail.save(temp_thumb.name)
                thumbnail_path = temp_thumb.name
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, resumable=False),
            ).execute()

        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        conn = db()
        conn.execute(
            "INSERT INTO published_videos(youtube_video_id,channel_id,title,privacy_status,created_at,youtube_url) VALUES(?,?,?,?,?,?)",
            (video_id, account["channel_id"], title, privacy, datetime.now(timezone.utc).isoformat(), youtube_url),
        )
        conn.commit()
        conn.close()
        return jsonify({
            "ok": True,
            "video_id": video_id,
            "youtube_url": youtube_url,
            "privacy_status": privacy,
            "channel_id": account["channel_id"],
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        for path in (video_path, thumbnail_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


@app.get("/api/publish/history")
def publish_history():
    conn = db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM published_videos ORDER BY created_at DESC LIMIT 50"
    )]
    conn.close()
    return jsonify(rows)


init_oauth_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
