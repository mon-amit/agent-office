#!/usr/bin/env python3
"""notion_idb_watch.py -- passive flush-cadence probe for Notion's IndexedDB log.

Question it answers: when a Notion AI chat actually runs, does Notion's local
IndexedDB write-ahead log grow within SECONDS (usable as a live agent-activity
signal for Agent Office) or only after minutes (not usable)?

Correlating timestamps embedded in the log against its mtime turned out to be
ambiguous -- a record written now can legitimately carry an older content
timestamp -- so this measures the only thing that isn't ambiguous: observed
size/mtime transitions in wall-clock time.

Read-only. Records ONLY sizes, mtimes and event-label COUNTS -- never message
content. Append-only log so it survives restarts; safe to leave running.

    python3 notion_idb_watch.py            # watch, print transitions
    python3 notion_idb_watch.py --report   # summarise what has been observed
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import time

IDB = os.path.expanduser(
    "~/Library/Application Support/Notion/Partitions/notion/IndexedDB/"
    "https_app.notion.com_0.indexeddb.leveldb"
)
OUT = os.path.expanduser("~/.notion-office/idb-flush-observations.tsv")
# labels that mark agent-run lifecycle in Notion's own client actions
LABELS = [
    "addStepsToExistingThreadAndRun",   # a chat run STARTS
    "updateThreadUpdatedTime",          # thread touched
    "StopInference",                    # user stopped a run
    "handleAbort",                      # run aborted
]


def live_log():
    """Newest .log file in the store (LevelDB rotates them)."""
    try:
        logs = [os.path.join(IDB, f) for f in os.listdir(IDB) if f.endswith(".log")]
    except OSError:
        return None
    return max(logs, key=os.path.getmtime) if logs else None


def label_counts(path):
    """Count lifecycle labels via `strings` -- never returns file content."""
    try:
        out = subprocess.run(["strings", path], capture_output=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    text = out.decode("utf-8", "ignore")
    return {lbl: len(re.findall(re.escape(lbl), text)) for lbl in LABELS}


def notion_running():
    try:
        return subprocess.run(["pgrep", "-x", "Notion"], capture_output=True).returncode == 0
    except OSError:
        return False


def report():
    if not os.path.exists(OUT):
        sys.exit(f"No observations yet at {OUT} -- run the watcher first.")
    rows = [l.rstrip("\n").split("\t") for l in open(OUT) if not l.startswith("#")]
    rows = [r for r in rows if len(r) >= 5]
    if not rows:
        sys.exit("No transitions recorded yet.")
    print(f"{len(rows)} write transition(s) observed\n")
    print(f"{'detected_at':20} {'lag_s':>7}  {'d_size':>9}  run_starts_delta")
    lags = []
    for r in rows:
        detected, mtime_s, dsize, dstarts = r[0], float(r[2]), r[3], r[4]
        lag = float(r[1])
        lags.append(lag)
        print(f"{detected:20} {lag:7.1f}  {dsize:>9}  {dstarts}")
    print()
    print(f"detection lag: min {min(lags):.1f}s  max {max(lags):.1f}s  "
          f"(lag = how long after the file's own mtime we noticed; poll interval bounds this)")
    starts = sum(int(r[4]) for r in rows if r[4].lstrip('+-').isdigit())
    print(f"total new run-start events seen: {starts}")
    print("\nVERDICT GUIDE: run-start deltas appearing in a transition whose mtime is within")
    print("a few seconds of a chat you just sent => log IS a usable live signal.")
    print("Run-start deltas only showing up minutes later => not real-time.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=5.0, help="poll seconds (default 5)")
    ap.add_argument("--report", action="store_true", help="summarise observations and exit")
    args = ap.parse_args()

    if args.report:
        return report()

    path = live_log()
    if not path:
        sys.exit(f"No .log file found under {IDB} -- is Notion desktop installed?")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if not os.path.exists(OUT):
        with open(OUT, "w") as fh:
            fh.write("# detected_at\tlag_s\tmtime_epoch\td_size\td_run_starts\tnotion_running\n")

    st = os.stat(path)
    prev_size, prev_mtime = st.st_size, st.st_mtime
    prev_counts = label_counts(path)
    print(f"watching {os.path.basename(path)}  size={prev_size}  "
          f"run_starts={prev_counts.get('addStepsToExistingThreadAndRun')}")
    print("Use Notion AI normally; transitions get appended to")
    print(f"  {OUT}")
    print("Then: python3 notion_idb_watch.py --report\n")

    while True:
        try:
            time.sleep(args.interval)
            cur = live_log() or path
            if cur != path:                      # LevelDB rotated to a new .log
                path = cur
                st = os.stat(path)
                prev_size, prev_mtime = st.st_size, st.st_mtime
                prev_counts = label_counts(path)
                print(f"[rotated] now watching {os.path.basename(path)}")
                continue
            st = os.stat(path)
            if st.st_mtime == prev_mtime and st.st_size == prev_size:
                continue
            counts = label_counts(path)
            d_starts = (counts.get("addStepsToExistingThreadAndRun", 0)
                        - prev_counts.get("addStepsToExistingThreadAndRun", 0))
            now = time.time()
            lag = now - st.st_mtime
            stamp = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
            line = (f"{stamp}\t{lag:.1f}\t{st.st_mtime:.0f}\t{st.st_size - prev_size:+d}\t"
                    f"{d_starts:+d}\t{'yes' if notion_running() else 'no'}\n")
            with open(OUT, "a") as fh:
                fh.write(line)
            print(f"WRITE  {stamp}  d_size={st.st_size - prev_size:+d}  "
                  f"run_starts={d_starts:+d}  noticed {lag:.1f}s after mtime", flush=True)
            prev_size, prev_mtime, prev_counts = st.st_size, st.st_mtime, counts
        except KeyboardInterrupt:
            print("\nbye!")
            return
        except Exception as e:                   # never let one bad cycle kill the watcher
            sys.stderr.write(f"watch: cycle failed, retrying: {e}\n")


if __name__ == "__main__":
    main()
