import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
E2E = ROOT / "production_test.py"
OUT = Path(os.environ.get("BATCH_OUT_DIR", "gemini_high_concurrency_batch"))
OUT.mkdir(parents=True, exist_ok=True)

PROBE_CONCURRENCIES = (6, 7, 8, 9)
PROBE_REPEATS = 3
PROBE_SEC = 120
COOLDOWN_SEC = 20
FULL_COOLDOWN_SEC = 30
MAX_HIGHER_FULLS = 2


def run_case(label, concurrency, limit_sec):
    report_path = OUT / f"{label}.json"
    transcript_path = OUT / f"{label}.txt"
    env = os.environ.copy()
    env.update({
        "FULL_CONCURRENCY": str(concurrency),
        "FULL_FALLBACK_CONCURRENCY": str(concurrency),
        "FULL_ADAPTIVE_BACKOFF": "0",
        "FULL_LIMIT_SEC": str(limit_sec),
        "FULL_REPORT_PATH": str(report_path),
        "FULL_TRANSCRIPT_PATH": str(transcript_path),
        "PYTHONUNBUFFERED": "1",
    })
    env.pop("GITHUB_STEP_SUMMARY", None)

    print(f"\n=== {label}: c={concurrency}, limit={limit_sec or 'FULL'}s ===", flush=True)
    started = time.monotonic()
    proc = subprocess.run([sys.executable, str(E2E)], env=env)
    elapsed = time.monotonic() - started

    if proc.returncode != 0 or not report_path.exists():
        return {
            "label": label,
            "concurrency": concurrency,
            "limit_sec": limit_sec,
            "process_exit": proc.returncode,
            "runner_elapsed_sec": round(elapsed, 3),
            "ok": False,
            "error": "process failed or report missing",
        }

    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "label": label,
        "concurrency": concurrency,
        "limit_sec": limit_sec,
        "process_exit": proc.returncode,
        "runner_elapsed_sec": round(elapsed, 3),
        "ok": bool(report.get("validated")),
        "main_empty_non_silent": int(report.get("main_empty_non_silent", 999)),
        "blocking_empty_non_silent": int(report.get("blocking_empty_non_silent", 999)),
        "wall_clock_min": report.get("wall_clock_min"),
        "chunks_total": report.get("chunks_total"),
        "chunks_usable": report.get("chunks_usable"),
        "suspicious_short_non_silent": report.get("suspicious_short_non_silent"),
        "total_words": report.get("total_words"),
        "mode_counts": report.get("mode_counts"),
        "report_path": str(report_path),
        "transcript_path": str(transcript_path),
    }


def clean_probe(result):
    return (
        result.get("ok")
        and result.get("blocking_empty_non_silent") == 0
        and result.get("main_empty_non_silent", 999) <= 1
    )


def main():
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    batch = {
        "started_at_utc": started_utc,
        "probe_seconds": PROBE_SEC,
        "probe_repeats": PROBE_REPEATS,
        "probe_concurrencies": list(PROBE_CONCURRENCIES),
        "probes": [],
        "full_runs": [],
    }

    print(
        f"Batch plan: {PROBE_REPEATS}x {PROBE_SEC}s probes for c="
        f"{','.join(map(str, PROBE_CONCURRENCIES))}; then full c=6 plus up to "
        f"{MAX_HIGHER_FULLS} qualifying higher settings.",
        flush=True,
    )

    per_c = {}
    for c in PROBE_CONCURRENCIES:
        per_c[c] = []
        for repeat in range(1, PROBE_REPEATS + 1):
            result = run_case(f"probe_c{c}_r{repeat}", c, PROBE_SEC)
            per_c[c].append(result)
            batch["probes"].append(result)
            print(
                f"probe c={c} r={repeat}: clean={clean_probe(result)} | "
                f"main_empty={result.get('main_empty_non_silent')} | "
                f"wall={result.get('wall_clock_min')}m",
                flush=True,
            )
            if not (c == PROBE_CONCURRENCIES[-1] and repeat == PROBE_REPEATS):
                time.sleep(COOLDOWN_SEC)

    probe_summary = {}
    qualified_higher = []
    for c, results in per_c.items():
        clean_count = sum(clean_probe(r) for r in results)
        walls = [
            float(r["wall_clock_min"])
            for r in results
            if r.get("wall_clock_min") is not None
        ]
        median_wall = statistics.median(walls) if walls else 999.0
        probe_summary[c] = {
            "clean_runs": clean_count,
            "repeats": len(results),
            "median_wall_clock_min": round(median_wall, 3),
            "main_empty_counts": [r.get("main_empty_non_silent") for r in results],
        }
        if c > 6 and clean_count >= 2:
            qualified_higher.append(c)

    batch["probe_summary"] = probe_summary
    qualified_higher.sort(
        key=lambda c: (
            -probe_summary[c]["clean_runs"],
            probe_summary[c]["median_wall_clock_min"],
            c,
        )
    )

    full_candidates = [6] + qualified_higher[:MAX_HIGHER_FULLS]
    batch["full_candidates"] = full_candidates
    print(f"\nFull-run candidates: {full_candidates}", flush=True)

    for i, c in enumerate(full_candidates):
        result = run_case(f"full_c{c}", c, 0)
        batch["full_runs"].append(result)
        print(
            f"FULL c={c}: validated={result.get('ok')} | "
            f"main_empty={result.get('main_empty_non_silent')} | "
            f"wall={result.get('wall_clock_min')}m | words={result.get('total_words')}",
            flush=True,
        )
        if i < len(full_candidates) - 1:
            time.sleep(FULL_COOLDOWN_SEC)

    batch["finished_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    batch_path = OUT / "batch_summary.json"
    batch_path.write_text(json.dumps(batch, indent=2, ensure_ascii=False), encoding="utf-8")

    md = [
        "# Gemini 3.5 high-concurrency batch",
        "",
        f"Probe: {PROBE_REPEATS} x {PROBE_SEC}s per concurrency.",
        "",
        "| c | clean probes | median probe min | main-empty counts |",
        "|---:|---:|---:|---|",
    ]
    for c in PROBE_CONCURRENCIES:
        x = probe_summary[c]
        md.append(
            f"| {c} | {x['clean_runs']}/{x['repeats']} | "
            f"{x['median_wall_clock_min']:.3f} | {x['main_empty_counts']} |"
        )

    md += [
        "",
        "## Full runs",
        "",
        "| c | validated | wall min | main empty | words |",
        "|---:|:---:|---:|---:|---:|",
    ]
    for r in batch["full_runs"]:
        md.append(
            f"| {r['concurrency']} | {r.get('ok')} | {r.get('wall_clock_min')} | "
            f"{r.get('main_empty_non_silent')} | {r.get('total_words')} |"
        )

    summary_md = "\n".join(md) + "\n"
    (OUT / "batch_summary.md").write_text(summary_md, encoding="utf-8")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary_md)

    print(f"\nSaved batch summary: {batch_path}", flush=True)


if __name__ == "__main__":
    main()
