import os
import re
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "nicheradar.db")
KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
app = Flask(__name__, static_folder="static", static_url_path="/static")

STOP = {
    "de", "la", "el", "los", "las", "un", "una", "y", "o", "en", "para", "por", "con", "sin",
    "que", "como", "cómo", "del", "al", "es", "son", "the", "a", "an", "and", "or", "in", "on",
    "for", "to", "of", "with", "is", "are", "how", "why", "what", "your", "you", "from", "más", "menos"
}


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channels(
            youtube_id TEXT PRIMARY KEY,
            handle TEXT,
            title TEXT,
            thumbnail TEXT,
            subscribers INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            videos INTEGER DEFAULT 0,
            uploads_playlist_id TEXT,
            published_at TEXT,
            category_hint TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos(
            youtube_id TEXT PRIMARY KEY,
            channel_id TEXT,
            title TEXT,
            published_at TEXT,
            duration_seconds INTEGER,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            thumbnail TEXT,
            fetched_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            captured_at TEXT,
            subscribers INTEGER,
            views INTEGER,
            videos INTEGER
        )
    """)

    existing = {r["name"] for r in conn.execute("PRAGMA table_info(channels)")}
    for name, sql_type in [("published_at", "TEXT"), ("category_hint", "TEXT")]:
        if name not in existing:
            conn.execute(f"ALTER TABLE channels ADD COLUMN {name} {sql_type}")
    conn.commit()
    conn.close()


def yt(endpoint, params):
    if not KEY:
        raise RuntimeError("Configura YOUTUBE_API_KEY")
    p = dict(params)
    p["key"] = KEY
    response = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}", params=p, timeout=30)
    response.raise_for_status()
    return response.json()


def batched(values, size=50):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def parse_target(value):
    value = value.strip()
    if value.startswith("@"):
        return "handle", value[1:]
    if "youtube.com" not in value:
        return ("id", value) if value.startswith("UC") else ("handle", value.lstrip("@"))
    path = urlparse(value).path.strip("/")
    if path.startswith("@"):
        return "handle", path[1:].split("/")[0]
    parts = path.split("/")
    if len(parts) > 1 and parts[0] == "channel":
        return "id", parts[1]
    return "query", parts[-1]


def resolve_channel(value):
    kind, target = parse_target(value)
    if kind == "id":
        data = yt("channels", {"part": "snippet,statistics,contentDetails", "id": target})
    elif kind == "handle":
        data = yt("channels", {"part": "snippet,statistics,contentDetails", "forHandle": target})
    else:
        search = yt("search", {"part": "snippet", "type": "channel", "q": target, "maxResults": 1})
        if not search.get("items"):
            return None
        cid = search["items"][0]["snippet"]["channelId"]
        data = yt("channels", {"part": "snippet,statistics,contentDetails", "id": cid})
    return data["items"][0] if data.get("items") else None


def normalize_channel(item, category_hint=""):
    snippet = item["snippet"]
    stats = item.get("statistics", {})
    details = item.get("contentDetails", {})
    thumbs = snippet.get("thumbnails", {})
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    uploads = (details.get("relatedPlaylists") or {}).get("uploads", "")
    return {
        "youtube_id": item["id"],
        "handle": snippet.get("customUrl", ""),
        "title": snippet.get("title", ""),
        "thumbnail": thumb,
        "subscribers": int(stats.get("subscriberCount", 0) or 0),
        "views": int(stats.get("viewCount", 0) or 0),
        "videos": int(stats.get("videoCount", 0) or 0),
        "uploads_playlist_id": uploads,
        "published_at": snippet.get("publishedAt", ""),
        "category_hint": category_hint,
    }


def save_channel(channel):
    conn = db()
    conn.execute("""
        INSERT INTO channels(
            youtube_id, handle, title, thumbnail, subscribers, views, videos,
            uploads_playlist_id, published_at, category_hint, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(youtube_id) DO UPDATE SET
            handle=excluded.handle,
            title=excluded.title,
            thumbnail=excluded.thumbnail,
            subscribers=excluded.subscribers,
            views=excluded.views,
            videos=excluded.videos,
            uploads_playlist_id=excluded.uploads_playlist_id,
            published_at=excluded.published_at,
            category_hint=CASE WHEN excluded.category_hint != '' THEN excluded.category_hint ELSE channels.category_hint END
    """, (
        channel["youtube_id"], channel["handle"], channel["title"], channel["thumbnail"],
        channel["subscribers"], channel["views"], channel["videos"], channel["uploads_playlist_id"],
        channel["published_at"], channel["category_hint"], datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()


def snapshot_channel(channel):
    conn = db()
    previous = conn.execute("""
        SELECT * FROM channel_snapshots
        WHERE channel_id = ?
        ORDER BY captured_at DESC LIMIT 1
    """, (channel["youtube_id"],)).fetchone()
    conn.execute("""
        INSERT INTO channel_snapshots(channel_id, captured_at, subscribers, views, videos)
        VALUES (?, ?, ?, ?, ?)
    """, (
        channel["youtube_id"], datetime.utcnow().isoformat(), channel["subscribers"], channel["views"], channel["videos"]
    ))
    conn.commit()
    conn.close()
    return dict(previous) if previous else None


def parse_duration(value):
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    h, m, s = [int(x or 0) for x in match.groups()]
    return h * 3600 + m * 60 + s


def iso_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def playlist_video_ids(playlist_id, max_results=20):
    if not playlist_id:
        return []
    data = yt("playlistItems", {
        "part": "contentDetails",
        "playlistId": playlist_id,
        "maxResults": min(max_results, 50)
    })
    return [x["contentDetails"]["videoId"] for x in data.get("items", [])]


def save_video_details(channel_id, ids):
    if not ids:
        return 0
    count = 0
    conn = db()
    now = datetime.utcnow().isoformat()
    for batch in batched(ids, 50):
        data = yt("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch)
        })
        for item in data.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            details = item.get("contentDetails", {})
            thumbs = snippet.get("thumbnails", {})
            thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            conn.execute("""
                INSERT INTO videos(
                    youtube_id, channel_id, title, published_at, duration_seconds,
                    views, likes, comments, thumbnail, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(youtube_id) DO UPDATE SET
                    title=excluded.title,
                    published_at=excluded.published_at,
                    duration_seconds=excluded.duration_seconds,
                    views=excluded.views,
                    likes=excluded.likes,
                    comments=excluded.comments,
                    thumbnail=excluded.thumbnail,
                    fetched_at=excluded.fetched_at
            """, (
                item["id"], channel_id, snippet.get("title", ""), snippet.get("publishedAt", ""),
                parse_duration(details.get("duration", "")), int(stats.get("viewCount", 0) or 0),
                int(stats.get("likeCount", 0) or 0), int(stats.get("commentCount", 0) or 0), thumb, now
            ))
            count += 1
    conn.commit()
    conn.close()
    return count


def median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return 1
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def video_analysis(channel_id):
    conn = db()
    rows = [dict(r) for r in conn.execute("""
        SELECT * FROM videos WHERE channel_id = ?
        ORDER BY published_at DESC LIMIT 30
    """, (channel_id,))]
    conn.close()
    now = datetime.now(timezone.utc)
    for video in rows:
        published = iso_dt(video["published_at"])
        age_days = max((now - published).total_seconds() / 86400, 0.25) if published else 1
        video["age_days"] = round(age_days, 1)
        video["views_per_day"] = round(video["views"] / age_days, 1)
    base = median([v["views_per_day"] for v in rows if v["views_per_day"] > 0])
    for video in rows:
        video["outlier_score"] = round(video["views_per_day"] / max(base, 1), 2)
    rows.sort(key=lambda x: x["views_per_day"], reverse=True)
    return rows, base


def months_old(published_at):
    published = iso_dt(published_at)
    if not published:
        return 999
    return max(0.1, (datetime.now(timezone.utc) - published).days / 30.44)


def clamp(value):
    return round(max(0, min(100, value)), 1)


def channel_opportunity(channel, previous=None):
    videos, base_vpd = video_analysis(channel["youtube_id"])
    recent = [v for v in videos if v["age_days"] <= 30]
    outliers = [v for v in videos if v["outlier_score"] >= 2]
    views_sub = channel["views"] / max(channel["subscribers"], 1)
    age_months = months_old(channel.get("published_at", ""))

    velocity_score = clamp(math.log10(max(base_vpd, 1)) * 22)
    views_sub_score = clamp(math.sqrt(max(views_sub, 0)) * 10)
    outlier_score = clamp((len(outliers) / max(len(videos), 1)) * 300)
    freshness_score = 100 if age_months <= 6 else 85 if age_months <= 12 else 65 if age_months <= 24 else 40 if age_months <= 48 else 20
    activity_score = clamp((len(recent) / 8) * 100)

    observed_growth = None
    growth_score = None
    if previous:
        previous_at = iso_dt(previous["captured_at"])
        if previous_at:
            days = max((datetime.now(timezone.utc) - previous_at).total_seconds() / 86400, 0.01)
            delta_views = max(0, channel["views"] - previous["views"])
            observed_growth = round(delta_views / days, 1)
            growth_score = clamp(math.log10(max(observed_growth, 1)) * 22)

    weights = {
        "velocity": 0.30,
        "views_sub": 0.20,
        "outliers": 0.20,
        "freshness": 0.15,
        "activity": 0.15,
    }
    score = (
        velocity_score * weights["velocity"] +
        views_sub_score * weights["views_sub"] +
        outlier_score * weights["outliers"] +
        freshness_score * weights["freshness"] +
        activity_score * weights["activity"]
    )
    if growth_score is not None:
        score = score * 0.8 + growth_score * 0.2

    return {
        "youtube_id": channel["youtube_id"],
        "title": channel["title"],
        "handle": channel.get("handle", ""),
        "thumbnail": channel.get("thumbnail", ""),
        "subscribers": channel["subscribers"],
        "views": channel["views"],
        "video_count": channel["videos"],
        "age_months": round(age_months, 1),
        "category_hint": channel.get("category_hint", ""),
        "median_views_per_day": round(base_vpd, 1),
        "views_per_subscriber": round(views_sub, 1),
        "uploads_30d": len(recent),
        "outliers_2x": len(outliers),
        "observed_views_growth_per_day": observed_growth,
        "channel_score": round(score, 1),
        "components": {
            "velocity": velocity_score,
            "views_sub": views_sub_score,
            "outliers": outlier_score,
            "freshness": freshness_score,
            "activity": activity_score,
            "observed_growth": growth_score,
        },
        "top_videos": videos[:5],
    }


def tokens(text):
    return set(
        word for word in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", text.lower())
        if len(word) > 2 and word not in STOP and not word.isdigit()
    )


def jaccard(a, b):
    return len(a & b) / max(1, len(a | b))


def cluster_signal_videos(channels):
    signal_videos = []
    for channel in channels:
        for video in channel["top_videos"]:
            if video["outlier_score"] >= 1.5:
                item = dict(video)
                item["channel_title"] = channel["title"]
                item["channel_id"] = channel["youtube_id"]
                item["channel_score"] = channel["channel_score"]
                signal_videos.append(item)

    clusters = []
    for video in sorted(signal_videos, key=lambda x: (x["outlier_score"], x["views_per_day"]), reverse=True):
        video_tokens = tokens(video["title"])
        best_index, best_similarity = None, 0
        for index, cluster in enumerate(clusters):
            similarity = jaccard(video_tokens, cluster["tokens"])
            if similarity > best_similarity:
                best_index, best_similarity = index, similarity
        if best_index is not None and best_similarity >= 0.20:
            clusters[best_index]["videos"].append(video)
            clusters[best_index]["tokens"].update(video_tokens)
        else:
            clusters.append({"tokens": set(video_tokens), "videos": [video]})

    results = []
    for cluster in clusters:
        videos = cluster["videos"]
        channel_ids = {v["channel_id"] for v in videos}
        if not videos:
            continue
        weighted = defaultdict(float)
        for video in videos:
            for token in tokens(video["title"]):
                weighted[token] += min(video["outlier_score"], 8)
        terms = [term for term, _ in sorted(weighted.items(), key=lambda x: x[1], reverse=True)[:3]]
        label = " · ".join(terms).title() if terms else "Tema emergente"
        avg_outlier = sum(v["outlier_score"] for v in videos) / len(videos)
        avg_vpd = sum(v["views_per_day"] for v in videos) / len(videos)
        avg_channel_score = sum(v["channel_score"] for v in videos) / len(videos)
        validation = clamp(len(channel_ids) * 24 + len(videos) * 6)
        demand = clamp(avg_outlier * 15 + math.log10(max(avg_vpd, 1)) * 18)
        freshness = clamp(sum(1 for v in videos if v["age_days"] <= 30) * 22)
        opportunity = clamp(demand * 0.35 + validation * 0.30 + freshness * 0.20 + avg_channel_score * 0.15)
        results.append({
            "name": label,
            "opportunity_score": opportunity,
            "channel_count": len(channel_ids),
            "signal_video_count": len(videos),
            "avg_outlier": round(avg_outlier, 2),
            "avg_views_per_day": round(avg_vpd, 1),
            "evidence": [
                {
                    "channel": v["channel_title"],
                    "title": v["title"],
                    "outlier_score": v["outlier_score"],
                    "views_per_day": v["views_per_day"],
                }
                for v in sorted(videos, key=lambda x: x["outlier_score"], reverse=True)[:5]
            ]
        })
    results.sort(key=lambda x: x["opportunity_score"], reverse=True)
    return results[:12]


def discover_candidates(region="US", category_limit=8, channels_limit=20):
    categories_data = yt("videoCategories", {"part": "snippet", "regionCode": region})
    categories = [
        (item["id"], item["snippet"]["title"])
        for item in categories_data.get("items", [])
        if item.get("snippet", {}).get("assignable", False)
    ][:category_limit]

    channel_hints = {}
    for category_id, category_name in categories:
        popular = yt("videos", {
            "part": "snippet",
            "chart": "mostPopular",
            "regionCode": region,
            "videoCategoryId": category_id,
            "maxResults": 8,
        })
        for item in popular.get("items", []):
            channel_id = item.get("snippet", {}).get("channelId")
            if channel_id and channel_id not in channel_hints:
                channel_hints[channel_id] = category_name
            if len(channel_hints) >= channels_limit:
                break
        if len(channel_hints) >= channels_limit:
            break

    ids = list(channel_hints.keys())[:channels_limit]
    items = []
    for batch in batched(ids, 50):
        data = yt("channels", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch)
        })
        items.extend(data.get("items", []))

    scored = []
    for item in items:
        cid = item["id"]
        channel = normalize_channel(item, channel_hints.get(cid, ""))
        previous = snapshot_channel(channel)
        save_channel(channel)
        video_ids = playlist_video_ids(channel["uploads_playlist_id"], max_results=20)
        save_video_details(cid, video_ids)
        scored.append(channel_opportunity(channel, previous))

    scored.sort(key=lambda x: x["channel_score"], reverse=True)
    niches = cluster_signal_videos(scored)
    return {
        "region": region,
        "channels_scanned": len(scored),
        "channels": scored,
        "niches": niches,
        "note": "El primer escaneo usa señales actuales. Los escaneos posteriores también incorporan crecimiento observado entre snapshots.",
    }


@app.route("/")
def home():
    return send_from_directory("static", "index.html")


@app.get("/api/status")
def status():
    return jsonify({"mvp": "global-discovery", "youtube_api_configured": bool(KEY)})


@app.post("/api/discovery/run")
def run_discovery():
    body = request.get_json(silent=True) or {}
    region = str(body.get("region", "US")).upper()[:2]
    category_limit = max(1, min(int(body.get("category_limit", 8)), 15))
    channels_limit = max(5, min(int(body.get("channels_limit", 20)), 30))
    if not KEY:
        return jsonify({"error": "Configura YOUTUBE_API_KEY para ejecutar el radar global."}), 503
    try:
        return jsonify(discover_candidates(region, category_limit, channels_limit))
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 502
        return jsonify({"error": f"YouTube API respondió con error {code}."}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/channels")
def channels():
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM channels ORDER BY created_at DESC")]
    conn.close()
    return jsonify(rows)


@app.post("/api/channels")
def add_channel():
    body = request.get_json(force=True) or {}
    value = str(body.get("url", "")).strip()
    if not value:
        return jsonify({"error": "Falta el canal"}), 400
    if not KEY:
        return jsonify({"error": "Configura YOUTUBE_API_KEY"}), 503
    try:
        item = resolve_channel(value)
        if not item:
            return jsonify({"error": "Canal no encontrado"}), 404
        channel = normalize_channel(item)
        previous = snapshot_channel(channel)
        save_channel(channel)
        save_video_details(channel["youtube_id"], playlist_video_ids(channel["uploads_playlist_id"], 20))
        return jsonify(channel_opportunity(channel, previous))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
