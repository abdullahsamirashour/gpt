import asyncio
import json
import math
import statistics
import subprocess
import time
from collections import Counter
from pathlib import Path

import production_test as live

SPEEDS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
BASELINE_REPEATS = 2
REGIONS = 4
SOURCE_REGION_SEC = 60.0
CHUNK_SEC = 10.0
CONCURRENCY = 5
OUT = Path("gemini_speed_audio_benchmark")
OUT.mkdir(parents=True, exist_ok=True)


def make_speed_pcm(src, start_sec, source_sec, speed, dst):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start_sec:.3f}", "-t", f"{source_sec:.3f}",
            "-i", str(src), "-vn",
            "-filter:a", f"atempo={speed}",
            "-ac", "1", "-ar", str(live.SAMPLE_RATE),
            "-f", "s16le", str(dst),
        ],
        check=True,
    )
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"Failed to create {dst}")
    return dst.stat().st_size / live.BYTES_PER_SEC


def choose_regions(full_pcm, duration):
    starts = live.select_regions(full_pcm, duration)
    if len(starts) <= REGIONS:
        return starts
    # Keep speech-heavy selections but spread the manual review over the lecture.
    positions = [round(i * (len(starts) - 1) / (REGIONS - 1)) for i in range(REGIONS)]
    return [starts[i] for i in positions]


def chunk_jobs(region_index, pcm_path, duration, prefix):
    count = max(1, math.ceil(duration / CHUNK_SEC))
    span = duration / count
    jobs = []
    for i in range(count):
        start = i * span
        end = duration if i == count - 1 else (i + 1) * span
        jobs.append({
            "region": region_index,
            "chunk": i,
            "pcm": pcm_path,
            "start": start,
            "seconds": end - start,
            "label": f"{prefix}-r{region_index + 1}-c{i + 1}",
        })
    return jobs


async def run_jobs(client, jobs):
    started = time.monotonic()
    results = []

    for offset in range(0, len(jobs), CONCURRENCY):
        wave = jobs[offset:offset + CONCURRENCY]
        wave_results = await asyncio.gather(*[
            live.segment_with_retry(
                client,
                job["pcm"],
                job["start"],
                job["seconds"],
                job["label"],
            )
            for job in wave
        ])
        for job, result in zip(wave, wave_results):
            result["region"] = job["region"]
            result["chunk"] = job["chunk"]
        results.extend(wave_results)
        print(
            f"    wave {offset // CONCURRENCY + 1}: "
            f"{sum(r['ok'] for r in wave_results)}/{len(wave_results)} usable | "
            f"modes={dict(Counter(r['mode'] for r in wave_results))}",
            flush=True,
        )

    return results, time.monotonic() - started


def assemble_regions(results):
    by_region = {}
    for region in range(REGIONS):
        parts = [
            r["effective_text"]
            for r in sorted(
                (x for x in results if x["region"] == region),
                key=lambda x: x["chunk"],
            )
            if r["effective_text"].strip()
        ]
        by_region[region] = live.merge_overlap(parts)
    return by_region


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def compare_to_baselines(candidate, baseline_a, baseline_b):
    a = live.compare_text(baseline_a, candidate)
    b = live.compare_text(baseline_b, candidate)
    return {
        "vs_a": a,
        "vs_b": b,
        "mean_reference_recall": round(mean([
            a.get("reference_recall"),
            b.get("reference_recall"),
        ]), 3),
        "mean_length_ratio": round(mean([
            a.get("length_ratio"),
            b.get("length_ratio"),
        ]), 3),
        "mean_sequence_ratio": round(mean([
            a.get("sequence_ratio"),
            b.get("sequence_ratio"),
        ]), 3),
    }


async def main():
    src = live.download_test_audio()
    live.install_sdk()
    api_key = live.load_key()

    full_pcm = OUT / "full_source.pcm"
    duration = await asyncio.to_thread(live.ffmpeg_to_pcm, src, full_pcm)
    starts = choose_regions(full_pcm, duration)

    print(
        f"Speed-audio benchmark: model={live.MODEL_ID} | c={CONCURRENCY} | "
        f"{REGIONS} x {SOURCE_REGION_SEC:.0f}s source regions | speeds={SPEEDS}",
        flush=True,
    )
    print("Region starts:", ", ".join(f"{x:.1f}s" for x in starts), flush=True)

    prepared = {}
    for speed in SPEEDS:
        prepared[speed] = []
        for region, start in enumerate(starts):
            dst = OUT / f"r{region + 1}_speed_{speed:g}.pcm"
            sped_duration = await asyncio.to_thread(
                make_speed_pcm, src, start, SOURCE_REGION_SEC, speed, dst
            )
            prepared[speed].append((dst, sped_duration))

    client = live.genai.Client(api_key=api_key)
    runs = {}
    try:
        # Two independent 1x baselines quantify normal model-to-model variation.
        for repeat in range(1, BASELINE_REPEATS + 1):
            key = f"1.0x-r{repeat}"
            jobs = []
            for region, (pcm, sped_duration) in enumerate(prepared[1.0]):
                jobs.extend(chunk_jobs(region, pcm, sped_duration, key))
            print(f"\n[{key}] jobs={len(jobs)}", flush=True)
            results, wall = await run_jobs(client, jobs)
            runs[key] = {
                "speed": 1.0,
                "repeat": repeat,
                "wall_clock_sec": round(wall, 3),
                "results": results,
                "regions": assemble_regions(results),
            }

        for speed in SPEEDS[1:]:
            key = f"{speed:g}x"
            jobs = []
            for region, (pcm, sped_duration) in enumerate(prepared[speed]):
                jobs.extend(chunk_jobs(region, pcm, sped_duration, key))
            print(f"\n[{key}] jobs={len(jobs)}", flush=True)
            results, wall = await run_jobs(client, jobs)
            runs[key] = {
                "speed": speed,
                "repeat": 1,
                "wall_clock_sec": round(wall, 3),
                "results": results,
                "regions": assemble_regions(results),
            }
    finally:
        try:
            client.close()
        except Exception:
            pass

    baseline_a = runs["1.0x-r1"]["regions"]
    baseline_b = runs["1.0x-r2"]["regions"]

    baseline_stability = []
    for region in range(REGIONS):
        ab = live.compare_text(baseline_a[region], baseline_b[region])
        ba = live.compare_text(baseline_b[region], baseline_a[region])
        baseline_stability.append({
            "region": region + 1,
            "start_sec": round(starts[region], 3),
            "a_words": len(live.normalize_tokens(baseline_a[region])),
            "b_words": len(live.normalize_tokens(baseline_b[region])),
            "mean_recall": round(mean([
                ab.get("reference_recall"),
                ba.get("reference_recall"),
            ]), 3),
            "mean_sequence_ratio": round(mean([
                ab.get("sequence_ratio"),
                ba.get("sequence_ratio"),
            ]), 3),
        })

    baseline_recall = mean(x["mean_recall"] for x in baseline_stability)
    summaries = []

    for key, run in runs.items():
        if key.startswith("1.0x-"):
            continue

        region_metrics = []
        for region in range(REGIONS):
            metrics = compare_to_baselines(
                run["regions"][region],
                baseline_a[region],
                baseline_b[region],
            )
            metrics.update({
                "region": region + 1,
                "start_sec": round(starts[region], 3),
                "candidate_words": len(live.normalize_tokens(run["regions"][region])),
            })
            region_metrics.append(metrics)

        recalls = [x["mean_reference_recall"] for x in region_metrics]
        lengths = [x["mean_length_ratio"] for x in region_metrics]
        sequences = [x["mean_sequence_ratio"] for x in region_metrics]
        empty_chunks = sum(not r["effective_text"].strip() for r in run["results"])
        total_source_min = REGIONS * SOURCE_REGION_SEC / 60.0
        extrapolated_hour_min = run["wall_clock_sec"] / 60.0 / total_source_min * 60.0
        relative_to_natural = mean(recalls) / baseline_recall if baseline_recall else 0.0

        if relative_to_natural >= 0.90 and mean(lengths) >= 0.85 and empty_chunks == 0:
            screen = "strong"
        elif relative_to_natural >= 0.80 and mean(lengths) >= 0.75:
            screen = "manual-review"
        else:
            screen = "weak"

        summaries.append({
            "speed": run["speed"],
            "wall_clock_sec": run["wall_clock_sec"],
            "rough_hour_estimate_min": round(extrapolated_hour_min, 2),
            "ideal_hour_floor_min": round(60.0 / (CONCURRENCY * run["speed"]), 2),
            "empty_chunks_after_retry": empty_chunks,
            "mean_reference_recall": round(mean(recalls), 3),
            "mean_length_ratio": round(mean(lengths), 3),
            "mean_sequence_ratio": round(mean(sequences), 3),
            "relative_to_baseline_variability": round(relative_to_natural, 3),
            "auto_screen": screen,
            "regions": region_metrics,
        })

    report = {
        "model": live.MODEL_ID,
        "concurrency": CONCURRENCY,
        "source_region_sec": SOURCE_REGION_SEC,
        "region_starts_sec": [round(x, 3) for x in starts],
        "baseline_stability": baseline_stability,
        "baseline_mean_recall": round(baseline_recall, 3),
        "summaries": summaries,
        "runs": runs,
    }
    (OUT / "speed_benchmark.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    review = [
        "# Gemini 3.5 Live — audio speed-up review",
        "",
        f"Model: `{live.MODEL_ID}`  ",
        f"Concurrency: {CONCURRENCY}  ",
        f"Each region represents {SOURCE_REGION_SEC:.0f}s of the original recording.",
        "",
        "Automatic similarity is only a screening signal. Manual review decides whether medical content is preserved.",
        "",
        "## Summary",
        "",
        "| speed | auto | recall vs two 1x baselines | length ratio | empty chunks | measured rough hour | ideal floor |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for item in summaries:
        review.append(
            f"| {item['speed']:g}x | {item['auto_screen']} | "
            f"{item['mean_reference_recall']:.3f} | {item['mean_length_ratio']:.3f} | "
            f"{item['empty_chunks_after_retry']} | {item['rough_hour_estimate_min']:.1f} min | "
            f"{item['ideal_hour_floor_min']:.1f} min |"
        )

    review += [
        "",
        "## Natural 1x variation",
        "",
        f"Mean bidirectional recall between the two independent 1x runs: {baseline_recall:.3f}.",
        "",
    ]

    keys = ["1.0x-r1", "1.0x-r2"] + [f"{x:g}x" for x in SPEEDS[1:]]
    for region, start in enumerate(starts):
        review += [
            f"## Region {region + 1} — original start {start:.1f}s",
            "",
        ]
        for key in keys:
            text = runs[key]["regions"][region].strip() or "[EMPTY]"
            review += [f"### {key}", "", text, ""]

    (OUT / "manual_review.md").write_text("\n".join(review), encoding="utf-8")

    print("\n=== SUMMARY ===", flush=True)
    print(f"Natural 1x baseline recall: {baseline_recall:.3f}", flush=True)
    for item in summaries:
        print(
            f"{item['speed']:>4g}x | {item['auto_screen']:<13} | "
            f"recall={item['mean_reference_recall']:.3f} | "
            f"len={item['mean_length_ratio']:.3f} | "
            f"empty={item['empty_chunks_after_retry']} | "
            f"rough-hour={item['rough_hour_estimate_min']:.1f}m | "
            f"ideal-floor={item['ideal_hour_floor_min']:.1f}m",
            flush=True,
        )

    summary_path = Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", ""))
    if str(summary_path):
        with summary_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(review[:30]) + "\n")

    try:
        full_pcm.unlink(missing_ok=True)
        for variants in prepared.values():
            for pcm, _ in variants:
                pcm.unlink(missing_ok=True)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
