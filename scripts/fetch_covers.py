#!/usr/bin/env python3
"""fetch_covers.py -- one-time (re-runnable) fetch of Bookshelf/Library cover art.

Downloads a small cover THUMBNAIL image plus factual bibliographic metadata (publisher,
first-publish year, page count, subject tags, rating) for every book in cursor_office.py's
SHELF_BOOKS list, via the Open Library Search + Covers APIs -- a free, keyless, nonprofit
(archive.org-backed) API whose stated purpose is exactly this: giving apps a book's cover
image + catalog data to display. (Google Books' API was tried first but its keyless quota
was fully exhausted -- 429 on every request, even from a fresh IP -- so this uses Open
Library instead, which has no such quota wall for reasonable use like this.)

Deliberately does NOT fetch or store any book-description/jacket-copy prose -- only the
cover image and plain bibliographic facts (publisher, year, page count, subject tags,
rating) are saved. Subject tags are capped at 4 per book to keep them as short factual
labels, not an attempt to reproduce a catalog entry's full text.

Run this ONCE (or again later to pick up anything that failed/was missing -- it skips books
it already has, so re-running is cheap and safe):

    python3 scripts/fetch_covers.py

Output: covers/<index>.jpg (one per book that had a cover) + covers/meta.json (bibliographic
data for every book, keyed by index, including ones with no image). cursor_office.py serves
these locally (see the /covers/ route in Handler.do_GET) so the running app needs NO internet
access at all -- only this one-time fetch does.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
OFFICE_PY = os.path.join(REPO_ROOT, "cursor_office.py")
COVERS_DIR = os.path.join(REPO_ROOT, "covers")
META_PATH = os.path.join(COVERS_DIR, "meta.json")
USER_AGENT = "agent-office/1.0 (personal offline pixel-art office toy; cover art fetch)"


def load_shelf_books():
    """Extract the SHELF_BOOKS JS array out of cursor_office.py without needing a JS
    engine: it's a plain array of {t,a,g} object literals with single-quoted or
    double-quoted strings, so a small regex per-field parse is enough (safer + simpler
    than shelling out to node for a one-time offline script)."""
    src = open(OFFICE_PY, encoding="utf-8").read()
    m = re.search(r"const SHELF_BOOKS = \[(.*?)\n\];", src, re.S)
    if not m:
        sys.exit("Could not find SHELF_BOOKS in cursor_office.py -- did it move/rename?")
    body = m.group(1)
    entry_re = re.compile(
        r"\{t:\s*(['\"])(?P<t>(?:\\.|(?!\1).)*)\1\s*,\s*"
        r"a:\s*(['\"])(?P<a>(?:\\.|(?!\3).)*)\3\s*,\s*"
        r"g:\s*(['\"])(?P<g>\w+)\5\s*\}"
    )
    books = []
    for em in entry_re.finditer(body):
        def unesc(s):
            return s.replace("\\'", "'").replace('\\"', '"')
        books.append({"t": unesc(em.group("t")), "a": unesc(em.group("a")), "g": em.group("g")})
    if len(books) < 100:  # sanity check -- the real list has 140+
        sys.exit(f"Only parsed {len(books)} books -- regex likely out of sync with the file format.")
    return books


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def http_get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


PLACEHOLDER_MAX_BYTES = 1000  # Open Library returns a tiny 1x1 GIF (not a 404) when a
                              # cover id has no actual image -- anything this small is that,
                              # not a real cover, so treat it as "no cover" rather than saving it.


def fetch_one(book):
    """Returns (meta_dict, image_bytes_or_None)."""
    q = urllib.parse.urlencode({
        "title": book["t"], "author": book["a"], "limit": 1,
        "fields": "cover_i,publisher,first_publish_year,number_of_pages_median,subject,ratings_average,ratings_count",
    })
    url = "https://openlibrary.org/search.json?" + q
    try:
        data = http_get_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        return {"ok": False, "reason": str(e)}, None
    docs = data.get("docs") or []
    if not docs:
        return {"ok": False, "reason": "no_match"}, None
    d = docs[0]
    cover_i = d.get("cover_i")
    publishers = d.get("publisher") or []
    subjects = d.get("subject") or []
    meta = {
        "ok": bool(cover_i),
        "publisher": publishers[0] if publishers else None,
        "publishedDate": str(d["first_publish_year"]) if d.get("first_publish_year") else None,
        "pageCount": d.get("number_of_pages_median"),
        "categories": subjects[:4] if subjects else None,
        "avgRating": round(d["ratings_average"], 2) if d.get("ratings_average") else None,
        "ratingsCount": d.get("ratings_count") or None,
    }
    if not cover_i:
        return meta, None
    img_url = "https://covers.openlibrary.org/b/id/%s-L.jpg" % cover_i
    try:
        img_bytes = http_get_bytes(img_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        meta["ok"] = False
        meta["reason"] = "image_fetch_failed: %s" % e
        return meta, None
    if len(img_bytes) < PLACEHOLDER_MAX_BYTES:
        meta["ok"] = False
        meta["reason"] = "placeholder_image"
        return meta, None
    return meta, img_bytes


def main():
    books = load_shelf_books()
    os.makedirs(COVERS_DIR, exist_ok=True)
    meta = {}
    if os.path.exists(META_PATH):
        try:
            meta = json.load(open(META_PATH, encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}

    ok = fail = skipped = 0
    for i, book in enumerate(books):
        key = str(i)
        img_path = os.path.join(COVERS_DIR, "%d.jpg" % i)
        already = meta.get(key, {}).get("ok") and os.path.isfile(img_path)
        if already:
            skipped += 1
            continue
        entry, img_bytes = fetch_one(book)
        if img_bytes:
            with open(img_path, "wb") as fh:
                fh.write(img_bytes)
            ok += 1
        else:
            fail += 1
        meta[key] = entry
        if (i + 1) % 20 == 0:
            json.dump(meta, open(META_PATH, "w", encoding="utf-8"))
            print(f"  {i+1}/{len(books)}  ok={ok} fail={fail} skipped={skipped}")
        time.sleep(0.3)  # a real pace, not a burst -- gentle on the (free, keyless) API

    json.dump(meta, open(META_PATH, "w", encoding="utf-8"))
    print(f"DONE: {ok} covers fetched, {fail} had no match/image, {skipped} already had one "
          f"(total {len(books)} books). Wrote {META_PATH}")


if __name__ == "__main__":
    main()
