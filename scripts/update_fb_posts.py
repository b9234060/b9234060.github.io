"""Sync Facebook Page posts into fb_posts.json via official Meta Graph API, webhook payload, or owner Issue."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PAGE_ID = "104929964914302"
PAGE_URL = "https://www.facebook.com/imoneyx/"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(ROOT_DIR, "fb_posts.json")
ASSETS_FB_DIR = os.path.join(ROOT_DIR, "assets", "fb")
TZ_TAIPEI = timezone(timedelta(hours=8))

YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|live/|shorts/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)


def load_store():
    """Load existing fb_posts.json store or return default structure."""
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("posts"), list):
                    return data
        except Exception as exc:
            print(f"Warning: could not parse existing fb_posts.json: {exc}", file=sys.stderr)

    return {
        "pageId": PAGE_ID,
        "pageName": "I級投資客日記",
        "pageHandle": "imoneyx",
        "pageUrl": PAGE_URL,
        "intro": "槓桿諮詢單次50萬（附發票）",
        "category": "粉絲專頁 · 個人部落格",
        "email": "b9234060@gmail.com",
        "followers": 176,
        "lastUpdated": datetime.now(TZ_TAIPEI).isoformat(),
        "posts": [],
    }


def save_store(store):
    """Sort posts newest-first and write fb_posts.json cleanly."""
    store["lastUpdated"] = datetime.now(TZ_TAIPEI).isoformat()
    store["posts"] = sorted(
        store["posts"],
        key=lambda p: p.get("created_time", ""),
        reverse=True,
    )[:100]
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(store['posts'])} Facebook posts to {OUTPUT_PATH}")


def extract_youtube_id(text):
    """Extract first 11-char YouTube video ID from text if present."""
    if not text:
        return ""
    match = YOUTUBE_ID_RE.search(text)
    return match.group(1) if match else ""


def derive_title(message, explicit_title=""):
    """Derive a concise headline from explicit title or first non-empty line."""
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()[:140]
    for line in (message or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:140]
    return "I級投資客日記最新動態"


def classify_post(message, permalink_url="", youtube_id="", media_type=""):
    """Classify post into essay, live_share, or reel."""
    if youtube_id or "#直播" in (message or ""):
        return "live_share", "直播同步貼文"
    if "/reel/" in (permalink_url or "") or media_type == "video":
        if len(message or "") > 180:
            return "essay", "深度長文 × Reel"
        return "reel", "Reels 短影音動態"
    if len(message or "") >= 160:
        return "essay", "深度專欄長文"
    return "essay", "粉專觀點筆記"


def format_display_date(iso_str):
    """Format ISO timestamp into YYYY-MM-DD HH:MM in Taipei time."""
    if not iso_str:
        return datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d")
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:10]


def download_image_locally(image_url, post_id):
    """Download an image URL into assets/fb/ so expiring CDN links remain permanent."""
    if not image_url or not image_url.startswith("https://"):
        return ""
    # YouTube static thumbnails never expire; keep them directly
    if "i.ytimg.com/" in image_url:
        return image_url

    try:
        os.makedirs(ASSETS_FB_DIR, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", post_id)[:60]
        local_rel = f"./assets/fb/{safe_name}.jpg"
        local_abs = os.path.join(ROOT_DIR, "assets", "fb", f"{safe_name}.jpg")

        req = urllib.request.Request(
            image_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; imoneylove-sync/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if not ctype.startswith("image/"):
                return ""
            raw = resp.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                return ""
            with open(local_abs, "wb") as f:
                f.write(raw)
        return local_rel
    except Exception as exc:
        print(f"Warning: skipping image download for {post_id}: {exc}", file=sys.stderr)
        return ""


def normalize_and_upsert(store, raw_post):
    """Normalize a raw post dict and upsert into store['posts'] without losing archives."""
    message = (raw_post.get("message") or "").strip()
    permalink = (raw_post.get("permalink_url") or PAGE_URL).strip()
    created_time = (raw_post.get("created_time") or datetime.now(TZ_TAIPEI).isoformat()).strip()
    post_id = (raw_post.get("id") or "").strip()
    if not post_id:
        digest = hashlib.sha256(f"{permalink}|{created_time}|{message[:60]}".encode("utf-8")).hexdigest()[:16]
        post_id = f"fb_sync_{digest}"

    youtube_id = raw_post.get("youtubeId") or extract_youtube_id(
        f"{message}\n{raw_post.get('attachment_url', '')}"
    )
    category, type_label = classify_post(
        message, permalink, youtube_id, raw_post.get("media_type", "")
    )
    if raw_post.get("category") in {"essay", "reel", "live_share"}:
        category = raw_post["category"]
    if raw_post.get("typeLabel"):
        type_label = raw_post["typeLabel"]

    image = raw_post.get("image") or ""
    if not image and youtube_id:
        image = f"https://i.ytimg.com/vi/{youtube_id}/maxresdefault.jpg"
    elif image.startswith("https://") and "i.ytimg.com/" not in image:
        downloaded = download_image_locally(image, post_id)
        image = downloaded or image

    entry = {
        "id": post_id,
        "title": derive_title(message, raw_post.get("title", "")),
        "message": message,
        "created_time": created_time,
        "displayDate": raw_post.get("displayDate") or format_display_date(created_time),
        "category": category,
        "typeLabel": type_label,
        "permalink_url": permalink,
        "image": image,
        "youtubeId": youtube_id,
        "mood": raw_post.get("mood", ""),
    }

    existing_map = {p["id"]: p for p in store["posts"] if p.get("id")}
    # Avoid duplicating seeded items by matching permalink_url as secondary key
    for old_id, old_post in list(existing_map.items()):
        if old_post.get("permalink_url") == permalink and permalink != PAGE_URL:
            existing_map.pop(old_id, None)
            break

    existing_map[post_id] = entry
    store["posts"] = list(existing_map.values())
    return entry


def sync_from_graph_api(store, token):
    """Fetch recent posts using official Meta Graph API v21.0."""
    fields = (
        "id,message,created_time,full_picture,permalink_url,"
        "attachments{media_type,url,unshimmed_url,title,description}"
    )
    params = urllib.parse.urlencode(
        {"fields": fields, "limit": 25, "access_token": token}
    )
    url = f"https://graph.facebook.com/v21.0/{PAGE_ID}/posts?{params}"

    req = urllib.request.Request(url, headers={"User-Agent": "imoneylove-sync/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        print(f"Graph API error {exc.code}: {err_body}", file=sys.stderr)
        sys.exit(1)

    posts = payload.get("data", [])
    print(f"Fetched {len(posts)} posts from Meta Graph API.")
    for item in posts:
        if not item.get("message"):
            continue
        attachments = item.get("attachments", {}).get("data", [])
        att_url = ""
        media_type = ""
        if attachments:
            att_url = attachments[0].get("unshimmed_url") or attachments[0].get("url") or ""
            media_type = attachments[0].get("media_type", "")

        normalize_and_upsert(
            store,
            {
                "id": f"fb_graph_{item['id']}",
                "message": item.get("message", ""),
                "created_time": item.get("created_time", ""),
                "permalink_url": item.get("permalink_url", PAGE_URL),
                "image": item.get("full_picture", ""),
                "attachment_url": att_url,
                "media_type": media_type,
            },
        )


def sync_from_payload(store, payload_path):
    """Ingest one post or list of posts from webhook JSON payload or GitHub event file."""
    with open(payload_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and isinstance(data.get("client_payload"), dict):
        data = data["client_payload"]

    items = data if isinstance(data, list) else data.get("posts", [data])
    for item in items:
        if isinstance(item, dict) and (item.get("message") or item.get("title")):
            normalize_and_upsert(store, item)


def sync_from_issue(store, issue_path):
    """Parse an owner-created GitHub Issue event JSON into a website FB article."""
    with open(issue_path, "r", encoding="utf-8") as f:
        event = json.load(f)

    issue = event.get("issue", event)
    raw_title = (issue.get("title") or "").strip()
    clean_title = re.sub(r"^\[FB\]\s*", "", raw_title, flags=re.IGNORECASE).strip()
    raw_body = (issue.get("body") or "").strip()
    created_time = issue.get("created_at") or datetime.now(TZ_TAIPEI).isoformat()

    permalink = PAGE_URL
    image_url = ""
    message_lines = []

    for line in raw_body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(("原文連結:", "原文連結：", "link:", "url:")):
            val = stripped.split(":", 1)[1].split("：", 1)[-1].strip()
            if val.startswith("https://"):
                permalink = val
        elif stripped.lower().startswith(("圖片:", "圖片：", "image:")):
            val = stripped.split(":", 1)[1].split("：", 1)[-1].strip()
            if val.startswith("https://"):
                image_url = val
        else:
            message_lines.append(line)

    message = "\n".join(message_lines).strip()
    if not message:
        message = clean_title

    normalize_and_upsert(
        store,
        {
            "id": f"fb_issue_{issue.get('number', 'manual')}",
            "title": clean_title,
            "message": message,
            "created_time": created_time,
            "permalink_url": permalink,
            "image": image_url,
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Sync Facebook posts into fb_posts.json")
    parser.add_argument("--from-payload", help="Path to webhook JSON payload")
    parser.add_argument("--from-issue", help="Path to GitHub Issue event JSON")
    args = parser.parse_args()

    store = load_store()

    if args.from_payload:
        sync_from_payload(store, args.from_payload)
        save_store(store)
        return

    if args.from_issue:
        sync_from_issue(store, args.from_issue)
        save_store(store)
        return

    token = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
    if not token:
        print(
            "FB_PAGE_ACCESS_TOKEN is not configured yet. "
            "Preserving existing fb_posts.json cleanly (no-op)."
        )
        return

    sync_from_graph_api(store, token)
    save_store(store)


if __name__ == "__main__":
    main()
