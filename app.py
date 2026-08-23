import os
import re
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
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
    os.makedirs(os.path.dirname(DB), exist_ok=True)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS radar_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            finished_at TEXT,
            region TEXT,
            category_limit INTEGER,
            channels_limit INTEGER,
            discovery_mode TEXT,
            channels_scanned INTEGER DEFAULT 0,
            niches_found INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS radar_run_channels(
            run_id INTEGER,
            channel_id TEXT,
            position INTEGER,
            channel_score REAL,
            momentum REAL,
            outliers REAL,
            audience_efficiency REAL,
            freshness REAL,
            consistency REAL,
            observed_growth_per_day REAL,
            created_at TEXT,
            PRIMARY KEY(run_id, channel_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_channel_time ON channel_snapshots(channel_id, captured_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_radar_runs_time ON radar_runs(started_at)")

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
        channel["published_at"], channel["category_hint"], datetime.now(timezone.utc).isoformat()
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
        channel["youtube_id"], datetime.now(timezone.utc).isoformat(), channel["subscribers"], channel["views"], channel["videos"]
    ))
    conn.commit()
    conn.close()
    return dict(previous) if previous else None


def snapshot_history(channel_id, limit=30):
    conn = db()
    rows = [dict(r) for r in conn.execute("""
        SELECT captured_at, subscribers, views, videos
        FROM channel_snapshots
        WHERE channel_id=?
        ORDER BY captured_at DESC LIMIT ?
    """, (channel_id, limit))]
    conn.close()
    return rows


def begin_radar_run(region, category_limit, channels_limit, discovery_mode):
    conn = db()
    cur = conn.execute("""
        INSERT INTO radar_runs(started_at, region, category_limit, channels_limit, discovery_mode, status)
        VALUES (?, ?, ?, ?, ?, 'running')
    """, (datetime.now(timezone.utc).isoformat(), region, category_limit, channels_limit, discovery_mode))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def finish_radar_run(run_id, scored, niches, status="completed"):
    conn = db()
    conn.execute("""
        UPDATE radar_runs
        SET finished_at=?, channels_scanned=?, niches_found=?, status=?
        WHERE id=?
    """, (datetime.now(timezone.utc).isoformat(), len(scored), len(niches), status, run_id))
    for position, channel in enumerate(scored, start=1):
        comp = channel["components"]
        conn.execute("""
            INSERT OR REPLACE INTO radar_run_channels(
                run_id, channel_id, position, channel_score, momentum, outliers,
                audience_efficiency, freshness, consistency, observed_growth_per_day, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, channel["youtube_id"], position, channel["channel_score"], comp["momentum"],
            comp["outliers"], comp["audience_efficiency"], comp["freshness"], comp["consistency"],
            channel["observed_views_growth_per_day"], datetime.now(timezone.utc).isoformat()
        ))
    conn.commit()
    conn.close()


def fail_radar_run(run_id):
    conn = db()
    conn.execute("UPDATE radar_runs SET finished_at=?, status='failed' WHERE id=?", (datetime.now(timezone.utc).isoformat(), run_id))
    conn.commit()
    conn.close()


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
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def playlist_video_ids(playlist_id, max_results=30):
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
    now = datetime.now(timezone.utc).isoformat()
    for batch in batched(ids, 50):
        data = yt("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)})
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
        return 0
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
    base = median([v["views_per_day"] for v in rows if v["views_per_day"] > 0]) or 1
    for video in rows:
        video["outlier_score"] = round(video["views_per_day"] / max(base, 1), 2)
    return rows, base


def months_old(published_at):
    published = iso_dt(published_at)
    if not published:
        return 999
    return max(0.1, (datetime.now(timezone.utc) - published).days / 30.44)


def clamp(value):
    return round(max(0, min(100, value)), 1)


def observed_growth(channel, previous):
    if not previous:
        return None
    previous_at = iso_dt(previous["captured_at"])
    if not previous_at:
        return None
    days = max((datetime.now(timezone.utc) - previous_at).total_seconds() / 86400, 0.01)
    if days < 0.02:
        return None
    return round(max(0, channel["views"] - previous["views"]) / days, 1)


def growth_score_v2(channel, previous=None):
    videos, base_vpd = video_analysis(channel["youtube_id"])
    recent = [v for v in videos if v["age_days"] <= 30]
    previous_window = [v for v in videos if 30 < v["age_days"] <= 90]
    strong = [v for v in videos if v["outlier_score"] >= 2]
    signal = [v for v in recent if v["outlier_score"] >= 1.2]

    recent_vpd = median([v["views_per_day"] for v in recent]) or base_vpd
    older_vpd = median([v["views_per_day"] for v in previous_window])
    momentum_ratio = recent_vpd / max(older_vpd, 1) if older_vpd else 1
    momentum_proxy = clamp(35 + math.log10(max(recent_vpd, 1)) * 12 + max(0, math.log2(max(momentum_ratio, 0.25))) * 18)

    growth_per_day = observed_growth(channel, previous)
    if growth_per_day is not None:
        observed_component = clamp(math.log10(max(growth_per_day, 1)) * 24)
        momentum = clamp(momentum_proxy * 0.55 + observed_component * 0.45)
    else:
        momentum = momentum_proxy

    density = len(strong) / max(len(videos), 1)
    avg_strength = (sum(min(v["outlier_score"], 8) for v in strong) / len(strong)) if strong else 0
    outliers = clamp(density * 180 + avg_strength * 10)

    recent_views = sum(v["views"] for v in recent)
    recent_views_per_sub = recent_views / max(channel["subscribers"], 1)
    audience_efficiency = clamp(math.log10(1 + recent_views_per_sub) * 55)

    newest_signal_age = min((v["age_days"] for v in strong), default=999)
    signal_freshness = 100 if newest_signal_age <= 7 else 85 if newest_signal_age <= 30 else 65 if newest_signal_age <= 60 else 40
    channel_age = months_old(channel.get("published_at", ""))
    channel_freshness = 100 if channel_age <= 6 else 85 if channel_age <= 12 else 65 if channel_age <= 24 else 40 if channel_age <= 48 else 20
    freshness = clamp(signal_freshness * 0.7 + channel_freshness * 0.3)

    signal_ratio = len(signal) / max(len(recent), 1)
    repeatability = min(1, len(strong) / 3)
    upload_factor = min(1, len(recent) / 8)
    consistency = clamp(signal_ratio * 55 + repeatability * 30 + upload_factor * 15)

    score = clamp(
        momentum * 0.30 +
        outliers * 0.25 +
        audience_efficiency * 0.20 +
        freshness * 0.15 +
        consistency * 0.10
    )

    top_videos = sorted(videos, key=lambda x: (x["outlier_score"], x["views_per_day"]), reverse=True)[:5]
    return {
        "youtube_id": channel["youtube_id"],
        "title": channel["title"],
        "handle": channel.get("handle", ""),
        "thumbnail": channel.get("thumbnail", ""),
        "subscribers": channel["subscribers"],
        "views": channel["views"],
        "video_count": channel["videos"],
        "age_months": round(channel_age, 1),
        "category_hint": channel.get("category_hint", ""),
        "median_views_per_day": round(base_vpd, 1),
        "recent_views_per_subscriber": round(recent_views_per_sub, 2),
        "uploads_30d": len(recent),
        "outliers_2x": len(strong),
        "observed_views_growth_per_day": growth_per_day,
        "channel_score": score,
        "score_version": 2,
        "components": {
            "momentum": momentum,
            "outliers": outliers,
            "audience_efficiency": audience_efficiency,
            "freshness": freshness,
            "consistency": consistency,
        },
        "top_videos": top_videos,
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
        if not videos:
            continue
        channel_ids = {v["channel_id"] for v in videos}
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


def discover_channel_hints(region="US", category_limit=8, channels_limit=20, discovery_mode="balanced"):
    categories_data = yt("videoCategories", {"part": "snippet", "regionCode": region})
    categories = [
        (item["id"], item["snippet"]["title"])
        for item in categories_data.get("items", [])
        if item.get("snippet", {}).get("assignable", False)
    ][:category_limit]

    channel_hints = {}
    per_source_limit = max(4, math.ceil(channels_limit / max(len(categories), 1)))
    published_after = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")

    for category_id, category_name in categories:
        popular = yt("videos", {
            "part": "snippet",
            "chart": "mostPopular",
            "regionCode": region,
            "videoCategoryId": category_id,
            "maxResults": min(8, per_source_limit + 2),
        })
        for item in popular.get("items", []):
            cid = item.get("snippet", {}).get("channelId")
            if cid:
                channel_hints.setdefault(cid, category_name)

        if discovery_mode in {"balanced", "deep"}:
            recent = yt("search", {
                "part": "snippet",
                "type": "video",
                "order": "viewCount",
                "regionCode": region,
                "videoCategoryId": category_id,
                "publishedAfter": published_after,
                "maxResults": min(8, per_source_limit + 2),
            })
            for item in recent.get("items", []):
                cid = item.get("snippet", {}).get("channelId")
                if cid:
                    channel_hints.setdefault(cid, category_name)

        if len(channel_hints) >= channels_limit * 2:
            break

    return dict(list(channel_hints.items())[:channels_limit * 2])


def discover_candidates(region="US", category_limit=8, channels_limit=20, discovery_mode="balanced"):
    run_id = begin_radar_run(region, category_limit, channels_limit, discovery_mode)
    try:
        channel_hints = discover_channel_hints(region, category_limit, channels_limit, discovery_mode)
        ids = list(channel_hints.keys())
        items = []
        for batch in batched(ids, 50):
            data = yt("channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)})
            items.extend(data.get("items", []))

        candidate_channels = []
        for item in items:
            channel = normalize_channel(item, channel_hints.get(item["id"], ""))
            candidate_channels.append(channel)
        candidate_channels.sort(key=lambda c: (c["subscribers"] > 500000, c["subscribers"]))
        candidate_channels = candidate_channels[:channels_limit]

        scored = []
        for channel in candidate_channels:
            previous = snapshot_channel(channel)
            save_channel(channel)
            video_ids = playlist_video_ids(channel["uploads_playlist_id"], max_results=30)
            save_video_details(channel["youtube_id"], video_ids)
            scored.append(growth_score_v2(channel, previous))

        scored.sort(key=lambda x: x["channel_score"], reverse=True)
        niches = cluster_signal_videos(scored)
        finish_radar_run(run_id, scored, niches)
        return {
            "run_id": run_id,
            "region": region,
            "discovery_mode": discovery_mode,
            "channels_scanned": len(scored),
            "channels": scored,
            "niches": niches,
            "note": "Growth Opportunity Score v2: Momentum, Outliers, Audience Efficiency, Freshness y Consistency. Cada ejecución queda guardada para medir crecimiento real entre escaneos.",
        }
    except Exception:
        fail_radar_run(run_id)
        raise


@app.route("/")
def home():
    return send_from_directory("static", "index.html")


@app.get("/api/status")
def status():
    return jsonify({"mvp": "global-discovery", "score_version": 2, "youtube_api_configured": bool(KEY)})


@app.post("/api/discovery/run")
def run_discovery():
    body = request.get_json(silent=True) or {}
    region = str(body.get("region", "US")).upper()[:2]
    category_limit = max(1, min(int(body.get("category_limit", 8)), 15))
    channels_limit = max(5, min(int(body.get("channels_limit", 20)), 30))
    discovery_mode = str(body.get("discovery_mode", "balanced"))
    if discovery_mode not in {"light", "balanced", "deep"}:
        discovery_mode = "balanced"
    if not KEY:
        return jsonify({"error": "Configura YOUTUBE_API_KEY para ejecutar el radar global."}), 503
    try:
        return jsonify(discover_candidates(region, category_limit, channels_limit, discovery_mode))
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 502
        return jsonify({"error": f"YouTube API respondió con error {code}."}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/discovery/history")
def discovery_history():
    limit = max(1, min(int(request.args.get("limit", 20)), 100))
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM radar_runs ORDER BY started_at DESC LIMIT ?", (limit,))]
    conn.close()
    return jsonify(rows)


@app.get("/api/discovery/history/<int:run_id>")
def discovery_run_detail(run_id):
    conn = db()
    run = conn.execute("SELECT * FROM radar_runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        conn.close()
        return jsonify({"error": "Ejecución no encontrada"}), 404
    channels = [dict(r) for r in conn.execute("""
        SELECT r.*, c.title, c.handle, c.thumbnail, c.subscribers
        FROM radar_run_channels r
        LEFT JOIN channels c ON c.youtube_id=r.channel_id
        WHERE r.run_id=? ORDER BY r.position ASC
    """, (run_id,))]
    conn.close()
    return jsonify({"run": dict(run), "channels": channels})


@app.get("/api/channels/<channel_id>/history")
def channel_history(channel_id):
    return jsonify(snapshot_history(channel_id, limit=60))


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
        save_video_details(channel["youtube_id"], playlist_video_ids(channel["uploads_playlist_id"], 30))
        return jsonify(growth_score_v2(channel, previous))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=True)
