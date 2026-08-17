#!/usr/bin/env python3
"""fetch_covers_audible.py -- fallback cover fetch for the books Open Library couldn't
match (mostly indie/self-published LitRPG audiobooks it just doesn't index well).

These titles are literally audiobooks from Amit's own Audible library, so Audible's own
product page is actually the BEST source available -- it's the exact edition he owns, not
a best-guess print edition. Public product pages, no login needed to view.

For each ASIN:
  - fetch the product page
  - pull the cover image ID out of the page's own og:image tag, then request the CLEAN
    (no social-share-card overlay) rendition of that same image at a decent size
  - pull publisher/release-date out of the page's embedded JSON (factual data only --
    same policy as fetch_covers.py: no description/jacket-copy text is stored)

Re-runnable/resumable like fetch_covers.py -- skips indices that already have a real cover.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
COVERS_DIR = os.path.join(REPO_ROOT, "covers")
META_PATH = os.path.join(COVERS_DIR, "meta.json")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# index -> Audible ASIN/ISBN-style product id, for exactly the books Open Library missed.
ASINS = {
    6: "B07F9ZD3W8", 13: "B002V8N9VG", 17: "B002V9ZFSK", 22: "B01L082HJ2",
    34: "B074GG7MT1", 35: "B0CRLK2PZD", 41: "B07QY3KR81", 42: "B07D1B9SD5",
    44: "B0FNXCHM19", 53: "B078RV7BWR", 57: "B07QQZT27X", 58: "B07KCNSCXR",
    59: "B07BWLPG8M", 74: "B09PSSTFP3", 82: "1541404467", 83: "1541404475",
    84: "1541404548", 85: "1541404556", 86: "1515942457", 87: "1515942465",
    88: "1494548682", 89: "1977300502", 90: "1494548690", 91: "1705267920",
    92: "1705267947", 93: "B09N9ZGVD6", 94: "B0BH6Z68LZ", 95: "B0C1ZVR9QR",
    96: "B0DC74YPT3", 97: "B0G4N8XTS7", 98: "1541436458", 99: "1705236561",
    100: "B09PSVRV4J", 101: "B09XJ79C11", 102: "B0BX518ZS3", 103: "B0CJG27S9V",
    104: "B0D7XWC9YR", 105: "1705236545", 106: "B08Y97ZFVX", 107: "B09Y2D2D5T",
    108: "B094JZMCJX", 110: "B09MDMD85Z", 111: "B0CDCLSH6G", 112: "B09GCGC34C",
    113: "B076Y2FWGY", 126: "B002VAESAA", 135: "B07ZP9BGVX", 137: "B07Z6MV4TD",
    138: "B07YVL6K4G", 139: "B07XQLXGJW", 140: "B07YYKJW55", 142: "B07QTC89BM",
}


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def fetch_one(asin):
    """Returns (meta_dict, image_bytes_or_None)."""
    url = f"https://www.audible.com/pd/{asin}"
    try:
        html = http_get(url).decode("utf-8", "ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return {"ok": False, "reason": str(e)}, None

    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if not m:
        return {"ok": False, "reason": "no_og_image"}, None
    og_url = m.group(1)
    idm = re.search(r"/images/I/([A-Za-z0-9]+)\.", og_url)
    img_bytes = None
    if idm:
        clean_url = f"https://m.media-amazon.com/images/I/{idm.group(1)}._SL500_.jpg"
        try:
            img_bytes = http_get(clean_url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            img_bytes = None
    if not img_bytes:
        # fall back to the (composited) og:image itself rather than nothing
        try:
            img_bytes = http_get(og_url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            return {"ok": False, "reason": "image_fetch_failed: %s" % e}, None

    pub_m = re.search(r'"publisherName":"([^"]*)"', html)
    date_m = re.search(r'"releaseDate":"([^"]*)"', html)
    meta = {
        "ok": True,
        "publisher": pub_m.group(1) if pub_m else None,
        "publishedDate": date_m.group(1) if date_m else None,
        "pageCount": None,
        "categories": None,
        "avgRating": None,
        "ratingsCount": None,
        "source": "audible",
    }
    return meta, img_bytes


def main():
    os.makedirs(COVERS_DIR, exist_ok=True)
    meta = json.load(open(META_PATH, encoding="utf-8")) if os.path.exists(META_PATH) else {}

    ok = fail = skipped = 0
    for i, asin in ASINS.items():
        key = str(i)
        img_path = os.path.join(COVERS_DIR, "%d.jpg" % i)
        if meta.get(key, {}).get("ok") and os.path.isfile(img_path):
            skipped += 1
            continue
        entry, img_bytes = fetch_one(asin)
        if img_bytes and len(img_bytes) > 1000:
            with open(img_path, "wb") as fh:
                fh.write(img_bytes)
            ok += 1
        else:
            entry["ok"] = False
            fail += 1
        meta[key] = entry
        json.dump(meta, open(META_PATH, "w", encoding="utf-8"))
        print(f"  idx {i} ({asin}): {'OK' if entry.get('ok') else 'FAIL: ' + str(entry.get('reason'))}")
        time.sleep(0.5)

    print(f"DONE: {ok} covers fetched, {fail} failed, {skipped} already had one "
          f"(total {len(ASINS)} attempted)")


if __name__ == "__main__":
    main()
