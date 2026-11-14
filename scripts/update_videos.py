import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import json
import os
import time
from datetime import datetime, timezone

CHANNEL_ID = "UCyA2CTG24GR9-P2mgz61sOA"
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "videos.json")

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/"
}

def classify_video(title, desc):
    if "#直播" in title or ("第" in title and "集" in title and "簡報" not in title):
        return "live"
    if "簡報" in title:
        return "briefing"
    return "mindset"

def extract_summary_points(desc):
    points = []
    if not desc:
        return points
    lines = desc.splitlines()
    in_core = False
    for line in lines:
        s = line.strip()
        if "本集核心重點" in s:
            in_core = True
            continue
        if in_core:
            if s.startswith("•") or s.startswith("-") or s.startswith("▪"):
                points.append(s.lstrip("•-▪ ").strip())
            elif s == "" and len(points) > 0:
                continue
    return points[:4]

def extract_timestamps(desc):
    timestamps = []
    if not desc:
        return timestamps
    for line in desc.splitlines():
        s = line.strip()
        if len(s) > 8 and s[0:2].isdigit() and s[2] == ":" and s[3:5].isdigit():
            parts = s.split(" ", 1)
            if len(parts) == 2:
                timestamps.append({"time": parts[0], "label": parts[1].strip()})
    return timestamps[:8]

def fetch_videos():
    req = urllib.request.Request(
        RSS_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    xml_data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                xml_data = resp.read()
            break
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            if attempt == 2:
                print(f"Warning: YouTube RSS unavailable ({exc}); preserving existing videos.json.")
                return
            print(f"Attempt {attempt + 1} failed ({exc}), retrying in {2 ** attempt}s...")
            time.sleep(2 ** attempt)

    root = ET.fromstring(xml_data)
    entries = []

    for entry in root.findall("atom:entry", NS):
        video_id_el = entry.find("yt:videoId", NS)
        title_el = entry.find("atom:title", NS)
        published_el = entry.find("atom:published", NS)
        updated_el = entry.find("atom:updated", NS)
        media_group = entry.find("media:group", NS)

        video_id = video_id_el.text if video_id_el is not None else ""
        title = title_el.text if title_el is not None else ""
        published = published_el.text if published_el is not None else ""
        updated = updated_el.text if updated_el is not None else ""

        desc = ""
        views = 0
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

        if media_group is not None:
            desc_el = media_group.find("media:description", NS)
            if desc_el is not None and desc_el.text:
                desc = desc_el.text
            community = media_group.find("media:community", NS)
            if community is not None:
                stats = community.find("media:statistics", NS)
                if stats is not None and "views" in stats.attrib:
                    views = int(stats.attrib["views"])

        category = classify_video(title, desc)
        points = extract_summary_points(desc)
        timestamps = extract_timestamps(desc)

        entries.append({
            "videoId": video_id,
            "title": title,
            "published": published,
            "updated": updated,
            "views": views,
            "category": category,
            "thumbnail": thumbnail,
            "fallbackThumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "summaryPoints": points,
            "timestamps": timestamps,
            "description": desc[:1200]
        })

    # Load existing videos if any so we accumulate archive history over time
    existing_map = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                for item in old_data.get("videos", []):
                    existing_map[item["videoId"]] = item
        except Exception:
            pass

    for item in entries:
        existing_map[item["videoId"]] = item

    merged = sorted(existing_map.values(), key=lambda x: x.get("published", ""), reverse=True)

    payload = {
        "channelId": CHANNEL_ID,
        "channelTitle": "i級投資客日記",
        "channelHandle": "@imoneylove",
        "channelUrl": "https://www.youtube.com/@imoneylove",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "videos": merged[:60]
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Successfully updated {len(merged[:60])} videos to {OUTPUT_PATH}")

if __name__ == "__main__":
    fetch_videos()
