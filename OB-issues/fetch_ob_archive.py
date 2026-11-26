#!/usr/bin/env python3
"""
ITU Operational Bulletin archive downloader.

Crawls the ITU publications catalog (per-year listings, then per-issue
pages), collects the original files (MSW Word `.doc`, PDF) for the
Operational Bulletin issues, and stages them under OB-issues/ with a
checksummed manifest.

Scope control (see service-publications-docs#149):
  --missing   download issues NOT in the normalized corpus (extraction targets)
  --held      download issues already normalized (distribution-only)
  default: --missing

Resumable: existing files are skipped; the manifest is append-only JSONL.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

BASE = "https://www.itu.int"
YEAR_URL = BASE + "/en/publications/ITU-T/Pages/publications.aspx?parent=T-SP-OB&version_date={year}"
ISSUE_URL = BASE + "/en/publications/ITU-T/Pages/publications.aspx?parent=T-SP-OB.{no}-{year}"
STAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)))
HAVE_FILE = "/tmp/have.txt"
DELAY = 0.8
TIMEOUT = 120

mode = "missing"
if "--held" in sys.argv:
    mode = "held"

held = set()
if os.path.exists(HAVE_FILE):
    held = set(int(x) for x in open(HAVE_FILE).read().split())
missing = set(range(670, 955)) | {966} | set(range(1, 669))

index_path = os.path.join(STAGE, "index.json")
manifest_path = os.path.join(STAGE, "manifest.jsonl")


def log(msg):
    print(msg, flush=True)


def curl(url, out=None):
    cmd = ["curl", "-s", "-L", "--fail", "--max-time", str(TIMEOUT), url]
    if out:
        cmd += ["-o", out]
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0:
            return r.stdout if not out else True
        time.sleep(2 * (attempt + 1))
    return None if not out else False


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


years = sorted({int(y) for y in re.findall(r"version_date=(\d{4})", curl(YEAR_URL.format(year=1994)).decode("utf-8", "ignore"))})
log(f"catalog years: {years[0]}-{years[-1]} ({len(years)})")

issues = {}  # no -> {year, url}
for year in years:
    html = curl(YEAR_URL.format(year=year))
    if not html:
        log(f"YEAR {year}: fetch failed")
        continue
    html = html.decode("utf-8", "ignore")
    found = sorted({int(m) for m in re.findall(r"T-SP-OB\.(\d{3,4})-%s" % year, html)})
    for no in found:
        issues[no] = {"year": year, "url": ISSUE_URL.format(no=no, year=year)}
    log(f"YEAR {year}: {len(found)} issues ({found[0] if found else '-'}..{found[-1] if found else '-'})")
    time.sleep(DELAY)

with open(index_path, "w") as f:
    json.dump({str(k): v for k, v in sorted(issues.items())}, f, indent=1)
log(f"index written: {len(issues)} issues online")

targets = sorted(n for n in issues if (n in missing) if mode == "missing") or \
          sorted(n for n in issues if (n in held) if mode == "held")
if mode == "missing":
    targets = sorted(n for n in issues if n in missing)
else:
    targets = sorted(n for n in issues if n in held)
log(f"mode={mode}: {len(targets)} target issues online")

manifest = open(manifest_path, "a")
ok = fail = 0
for i, no in enumerate(targets):
    info = issues[no]
    year = info["year"]
    html = curl(info["url"])
    if not html:
        log(f"OB {no}: issue page fetch FAILED")
        fail += 1
        continue
    html = html.decode("utf-8", "ignore")
    files = sorted(set(re.findall(r'href="(/dms_pub/[^"]+\.(?:doc|docx|pdf))"', html, re.I)))
    d = os.path.join(STAGE, f"T-SP-OB.{no}-{year}")
    os.makedirs(d, exist_ok=True)
    for path in files:
        name = path.rsplit("/", 1)[-1]
        out = os.path.join(d, name)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            continue
        got = curl(BASE + path, out=out)
        if got and os.path.getsize(out) > 0:
            rec = {"issue": no, "year": year, "file": name, "bytes": os.path.getsize(out),
                   "sha256": sha256(out), "url": BASE + path}
            manifest.write(json.dumps(rec) + "\n")
            manifest.flush()
            ok += 1
        else:
            log(f"OB {no}: download FAILED {name}")
            fail += 1
        time.sleep(DELAY)
    log(f"[{i + 1}/{len(targets)}] OB {no} ({year}): {len(files)} files")
manifest.close()
log(f"DONE ok={ok} fail={fail}")
