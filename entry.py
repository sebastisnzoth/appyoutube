import math
from datetime import datetime, timezone, timedelta

import requests

import app as core
from oauth_app import app


def discover_channel_hints_safe(region="US", category_limit=8, channels_limit=20, discovery_mode="balanced"):
    data = core.yt("videoCategories", {"part": "snippet", "regionCode": region})
    categories = [
        (item["id"], item["snippet"]["title"])
        for item in data.get("items", [])
        if item.get("snippet", {}).get("assignable", False)
    ][:category_limit]

    hints = {}
    per = max(4, math.ceil(channels_limit / max(len(categories), 1)))
    published_after = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")

    for category_id, category_name in categories:
        try:
            popular = core.yt(
                "videos",
                {
                    "part": "snippet",
                    "chart": "mostPopular",
                    "regionCode": region,
                    "videoCategoryId": category_id,
                    "maxResults": min(8, per + 2),
                },
            )
            for item in popular.get("items", []):
                channel_id = item.get("snippet", {}).get("channelId")
                if channel_id:
                    hints.setdefault(channel_id, category_name)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise

        if discovery_mode in {"balanced", "deep"}:
            try:
                recent = core.yt(
                    "search",
                    {
                        "part": "snippet",
                        "type": "video",
                        "order": "viewCount",
                        "regionCode": region,
                        "videoCategoryId": category_id,
                        "publishedAfter": published_after,
                        "maxResults": min(8, per + 2),
                    },
                )
                for item in recent.get("items", []):
                    channel_id = item.get("snippet", {}).get("channelId")
                    if channel_id:
                        hints.setdefault(channel_id, category_name)
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 404:
                    raise

        if len(hints) >= channels_limit * 2:
            break

    return dict(list(hints.items())[: channels_limit * 2])


core.discover_channel_hints = discover_channel_hints_safe
