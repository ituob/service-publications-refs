#!/usr/bin/env python3
"""
Parallel variant of the OB archive downloader (8 workers).
Resumable: per-file skip-if-exists; manifest appends are lock-protected.
Usage: fetch_ob_archive_par.py --held
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BASE = "https://www.itu.int"
YEAR_URL = BASE + "/en/publications/ITU-T/Pages/publications.aspx?parent=T-SP-OB&version_date={year}"
ISSUE_URL = BASE + "/en/publications/ITU-T/Pages/publications.aspx?parent=T-SP-OB.{no}-{year}"
STAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)))
HAVE_FILE = "/tmp/have.txt"
TIMEOUT = 120
WORKERS = 8

manifest_lock = threading.Lock()
counter_lock = threading.Lock()
done = [0]

manifest = open(os.path.join(STAGE, "manifest.jsonl"), "a")


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
        time.sleep(1.5 * (attempt + 1))
    return None if not out else False


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def process_issue(no, year):
    html = curl(ISSUE_URL.format(no=no, year=year))
    if not html:
        log(f"OB {no}: page FAILED")
        return 0, 1
    html = html.decode("utf-8", "ignore")
    files = sorted(set(re.findall(r'href="(/dms_pub/[^"]+\.(?:doc|docx|pdf))"', html, re.I)))
    files = [f for f in files if "S-GEN-CAT" not in f]
    d = os.path.join(STAGE, f"T-SP-OB.{no}-{year}")
    os.makedirs(d, exist_ok=True)
    ok = fail = 0
    for path in files:
        name = path.rsplit("/", 1)[-1]
        out = os.path.join(d, name)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            ok += 1
            continue
        got = curl(BASE + path, out=out)
        if got and os.path.getsize(out) > 0:
            rec = {"issue": no, "year": year, "file": name, "bytes": os.path.getsize(out),
                   "sha256": sha256(out), "url": BASE + path}
            with manifest_lock:
                manifest.write(json.dumps(rec) + "\n")
                manifest.flush()
            ok += 1
        else:
            log(f"OB {no}: download FAILED {name}")
            fail += 1
    return ok, fail


held = set(int(x) for x in open(HAVE_FILE).read().split())
index = json.load(open(os.path.join(STAGE, "index.json")))
targets = sorted((int(k), v["year"]) for k, v in index.items() if int(k) in held)
log(f"held targets online: {len(targets)}")

total = len(targets)
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futures = {ex.submit(process_issue, no, year): no for no, year in targets}
    for fut in futures:
        pass
    for i, fut in enumerate(list(futures)):
        ok, fail = fut.result()
        with counter_lock:
            done[0] += 1
            log(f"[{done[0]}/{total}] OB {futures[fut]}: ok={ok} fail={fail}")

manifest.close()
log("DONE")
