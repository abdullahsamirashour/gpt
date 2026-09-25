#@title Gemini 3.5 Live - Chunk Integrity Benchmark { display-mode: "form" }
# Fully automatic: Colab Secret -> GitHub test audio -> correctness reference -> chunk-size benchmark -> c=6 estimate.

import os, sys, math, time, json, asyncio, subprocess, shutil, re, difflib
from array import array
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

MODEL_ID = "gemini-3.5-transcribe-live"
SDK_VERSION = "2.25.0"
SECRET_NAME = "GEMINI_API_KEY"
LANGUAGE_CODES = ["ar-EG", "en-US"]

TEST_AUDIO_NAME = "62_1790064377959_Food poisoning_compressed.ogg"
TEST_AUDIO_PATH = Path(__file__).resolve().with_name(TEST_AUDIO_NAME)

SAMPLE_RATE = 16_000
BYTES_PER_SEC = SAMPLE_RATE * 2
FRAME_MS = 100
FRAME_BYTES = BYTES_PER_SEC * FRAME_MS // 1000

CONCURRENCY = 6
REGION_COUNT = 6
REFERENCE_UNIT_SEC = 10
REFERENCE_SPAN_SEC = 60
CANDIDATE_CHUNK_SECS = (15, 12, 10)
FINAL_GRACE_SEC = 6.0
MAX_EMPTY_RETRIES = 1

# Strict enough to reject obviously truncated output while tolerating normal ASR variation.
MIN_LENGTH_RATIO = 0.60
MIN_REFERENCE_RECALL = 0.55
MIN_SCORABLE_REFERENCE_WORDS = 5
MIN_SCORABLE_REGIONS = 4

TMP = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "gemini35_chunk_bench"
TMP.mkdir(parents=True, exist_ok=True)


def load_key():
    key = (os.environ.get(SECRET_NAME) or "").strip()
    if not key:
        raise RuntimeError(f"Environment variable {SECRET_NAME} is missing.")
    print("[secret] Gemini API key loaded from GitHub Actions secret.")
    return key


def download_test_audio():
    path = TEST_AUDIO_PATH
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"Benchmark audio not found: {path}")
    print(f"[test audio] Using repository file: {path.name} | {path.stat().st_size / 1024 / 1024:.2f} MiB")
    return path


def install_sdk():
    global genai, types, installed_sdk
    from google import genai as _genai
    from google.genai import types as _types
    import importlib.metadata as _metadata
    genai, types = _genai, _types
    installed_sdk = _metadata.version("google-genai")
    if installed_sdk != SDK_VERSION:
        raise RuntimeError(f"Expected google-genai {SDK_VERSION}, got {installed_sdk}")
    print(f"[setup] google-genai=={installed_sdk}")


def ffmpeg_to_pcm(src, dst):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is unavailable in this Colab runtime.")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", str(dst),
        ],
        check=True,
    )
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError("Audio conversion failed.")
    return dst.stat().st_size / BYTES_PER_SEC


def manual_config():
    return types.LiveConnectConfig(
        response_modalities=["TEXT"],
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=LANGUAGE_CODES,
            mode="VERBATIM",
        ),
    )


def pcm_rms(path, start_sec, seconds):
    start = int(start_sec * BYTES_PER_SEC) // 2 * 2
    size = int(seconds * BYTES_PER_SEC) // 2 * 2
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read(size)
    samples = array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0
    return math.sqrt(sum(v * v for v in samples) / len(samples))


def select_regions(pcm_path, duration):
    span = min(float(REFERENCE_SPAN_SEC), duration)
    if duration <= span:
        return [0.0]

    # Score a grid, then greedily keep speech-heavy, non-overlapping windows.
    max_start = duration - span
    grid = [max_start * i / 23 for i in range(24)]
    scored = sorted(
        ((pcm_rms(pcm_path, s, min(12.0, span)), s) for s in grid),
        reverse=True,
    )

    chosen = []
    for _rms, start in scored:
        if all(abs(start - existing) >= span for existing in chosen):
            chosen.append(start)
        if len(chosen) == REGION_COUNT:
            break

    # If energy-based spacing did not produce enough, fill with evenly spaced windows.
    if len(chosen) < REGION_COUNT:
        for i in range(REGION_COUNT):
            start = max_start * i / max(1, REGION_COUNT - 1)
            if all(abs(start - existing) >= span * 0.8 for existing in chosen):
                chosen.append(start)
            if len(chosen) == REGION_COUNT:
                break

    return sorted(chosen[:REGION_COUNT])


def norm_word(word):
    return re.sub(r"[^\w\u0600-\u06FF]+", "", word, flags=re.UNICODE).casefold()


def merge_overlap(parts, max_words=50):
    merged = []
    for text in parts:
        words = (text or "").strip().split()
        if not words:
            continue
        if not merged:
            merged = words
            continue
        left = [norm_word(x) for x in merged]
        right = [norm_word(x) for x in words]
        best = 0
        for n in range(min(max_words, len(merged), len(words)), 1, -1):
            if left[-n:] == right[:n] and any(left[-n:]):
                best = n
                break
        merged.extend(words[best:])
    return " ".join(merged).strip()


def normalize_tokens(text):
    return [norm_word(w) for w in (text or "").split() if norm_word(w)]


def compare_text(reference, candidate):
    ref = normalize_tokens(reference)
    cand = normalize_tokens(candidate)

    if len(ref) < MIN_SCORABLE_REFERENCE_WORDS:
        return {
            "reference_words": len(ref),
            "candidate_words": len(cand),
            "length_ratio": None,
            "reference_recall": None,
            "sequence_ratio": None,
            "scorable": False,
            "pass": None,
        }

    if not cand:
        return {
            "reference_words": len(ref),
            "candidate_words": 0,
            "length_ratio": 0.0,
            "reference_recall": 0.0,
            "sequence_ratio": 0.0,
            "scorable": True,
            "pass": False,
        }

    ref_counts = Counter(ref)
    cand_counts = Counter(cand)
    overlap = sum((ref_counts & cand_counts).values())
    recall = overlap / len(ref)
    length_ratio = len(cand) / len(ref)
    seq = difflib.SequenceMatcher(None, ref, cand).ratio()

    # We care about missing reference speech. Extra candidate words are not
    # penalized here because the 10s reference can itself miss speech.
    passed = (
        length_ratio >= MIN_LENGTH_RATIO
        and recall >= MIN_REFERENCE_RECALL
    )

    return {
        "reference_words": len(ref),
        "candidate_words": len(cand),
        "length_ratio": round(length_ratio, 3),
        "reference_recall": round(recall, 3),
        "sequence_ratio": round(seq, 3),
        "scorable": True,
        "pass": passed,
    }


async def stream_audio(session, pcm_path, start_sec, seconds):
    start = int(start_sec * BYTES_PER_SEC) // 2 * 2
    remaining = int(seconds * BYTES_PER_SEC) // 2 * 2

    await session.send_realtime_input(activity_start=types.ActivityStart())

    with open(pcm_path, "rb", buffering=1024 * 1024) as fh:
        fh.seek(start)
        next_send = time.monotonic()
        while remaining > 0:
            data = fh.read(min(FRAME_BYTES, remaining))
            if not data:
                break
            await session.send_realtime_input(
                audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
            )
            remaining -= len(data)
            next_send += FRAME_MS / 1000.0
            delay = next_send - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)

    await session.send_realtime_input(activity_end=types.ActivityEnd())


async def receive_segment(session, state):
    try:
        async for response in session.receive():
            state["messages"] += 1
            sc = getattr(response, "server_content", None)
            if not sc:
                continue
            state["server_content_messages"] += 1

            interim = getattr(sc, "interim_input_transcription", None)
            if interim and (getattr(interim, "text", "") or "").strip():
                state["interim_events"] += 1
                state["latest_interim"] = interim.text.strip()

            final = getattr(sc, "input_transcription", None)
            if final and (getattr(final, "text", "") or "").strip():
                state["final_events"] += 1
                state["final_segments"].append(final.text.strip())

    except asyncio.CancelledError:
        raise
    except Exception as e:
        code = getattr(e, "code", None)
        if code != 1000 and not str(e).lstrip().startswith("1000"):
            state["receiver_error"] = f"{type(e).__name__}: {e}"


async def one_segment(client, pcm_path, start_sec, seconds, label, attempt=1):
    state = {
        "label": label,
        "start_sec": round(start_sec, 3),
        "audio_sec": round(seconds, 3),
        "attempt": attempt,
        "messages": 0,
        "server_content_messages": 0,
        "interim_events": 0,
        "final_events": 0,
        "final_segments": [],
        "latest_interim": "",
        "receiver_error": "",
        "api_error": "",
    }
    started = time.monotonic()
    recv = None

    try:
        async with client.aio.live.connect(model=MODEL_ID, config=manual_config()) as session:
            recv = asyncio.create_task(receive_segment(session, state))
            await stream_audio(session, pcm_path, start_sec, seconds)

            # A final commonly arrives a few seconds after activity_end.
            # If it never arrives but interim text exists, keep the last interim as a measured fallback.
            deadline = time.monotonic() + FINAL_GRACE_SEC
            while time.monotonic() < deadline:
                if state["final_events"]:
                    await asyncio.sleep(1.0)
                    break
                await asyncio.sleep(0.1)

    except Exception as e:
        state["api_error"] = f"{type(e).__name__}: {e}"
    finally:
        if recv is not None and not recv.done():
            recv.cancel()
            try:
                await recv
            except BaseException:
                pass

    final_text = merge_overlap(state["final_segments"])
    interim_text = state["latest_interim"].strip()

    # Final is authoritative, but on this recording we have observed a short
    # final followed by a materially longer interim hypothesis. For benchmark
    # coverage, use the more complete single hypothesis instead of concatenating
    # both and double-counting overlapping speech.
    if final_text and interim_text:
        if len(normalize_tokens(interim_text)) > len(normalize_tokens(final_text)):
            effective = interim_text
            mode = "interim-preferred"
        else:
            effective = final_text
            mode = "final-preferred"
    elif final_text:
        effective = final_text
        mode = "final"
    elif interim_text:
        effective = interim_text
        mode = "interim-fallback"
    else:
        effective = ""
        mode = "empty"

    state["effective_text"] = effective
    state["mode"] = mode
    state["ok"] = bool(effective) and not state["api_error"]
    state["elapsed_sec"] = round(time.monotonic() - started, 3)
    state["effective_words"] = len(normalize_tokens(effective))
    return state


async def segment_with_retry(client, pcm_path, start_sec, seconds, label):
    result = await one_segment(client, pcm_path, start_sec, seconds, label, 1)
    for attempt in range(2, MAX_EMPTY_RETRIES + 2):
        if result["ok"]:
            break
        await asyncio.sleep(1)
        result = await one_segment(client, pcm_path, start_sec, seconds, label, attempt)
    return result


async def run_wave(client, pcm_path, jobs):
    started = time.monotonic()
    results = await asyncio.gather(*[
        segment_with_retry(client, pcm_path, start, seconds, label)
        for start, seconds, label in jobs
    ])
    return results, time.monotonic() - started


def mode_counts(results):
    return dict(Counter(r["mode"] for r in results))


async def build_reference(client, pcm_path, regions):
    jobs = []
    units_per_region = REFERENCE_SPAN_SEC // REFERENCE_UNIT_SEC
    for region_i, region_start in enumerate(regions):
        for unit_i in range(units_per_region):
            start = region_start + unit_i * REFERENCE_UNIT_SEC
            jobs.append((
                start,
                REFERENCE_UNIT_SEC,
                f"reference-r{region_i+1}-u{unit_i+1}",
            ))

    print(
        f"\n[1] Building reference: {len(jobs)} independent "
        f"{REFERENCE_UNIT_SEC}s sessions in waves of {CONCURRENCY}"
    )

    all_results = []
    for offset in range(0, len(jobs), CONCURRENCY):
        wave_jobs = jobs[offset: offset + CONCURRENCY]
        results, elapsed = await run_wave(client, pcm_path, wave_jobs)
        all_results.extend(results)
        print(
            f"  wave {offset//CONCURRENCY + 1}: "
            f"{sum(r['ok'] for r in results)}/{len(results)} usable | "
            f"{elapsed:.1f}s | modes={mode_counts(results)}"
        )

    by_region = {}
    for region_i, region_start in enumerate(regions):
        region_results = [
            r for r in all_results
            if r["label"].startswith(f"reference-r{region_i+1}-")
        ]
        by_region[region_i] = {
            "start_sec": region_start,
            "results": region_results,
            "text": " ".join(r["effective_text"] for r in region_results if r["ok"]).strip(),
        }

    bad = [r for r in all_results if not r["ok"]]
    scorable_regions = sum(
        len(normalize_tokens(region["text"])) >= MIN_SCORABLE_REFERENCE_WORDS
        for region in by_region.values()
    )
    return {
        "ok": scorable_regions >= MIN_SCORABLE_REGIONS,
        "results": all_results,
        "regions": by_region,
        "bad_count": len(bad),
        "scorable_regions": scorable_regions,
    }


def reference_prefix(reference_region, seconds):
    units = max(1, int(math.ceil(seconds / REFERENCE_UNIT_SEC)))
    return " ".join(
        r["effective_text"]
        for r in reference_region["results"][:units]
        if r["ok"]
    ).strip()


async def test_candidate_size(client, pcm_path, reference, seconds):
    jobs = [
        (
            region["start_sec"],
            seconds,
            f"candidate-{seconds}s-r{region_i+1}",
        )
        for region_i, region in reference["regions"].items()
    ]

    results, elapsed = await run_wave(client, pcm_path, jobs)

    comparisons = []
    for region_i, result in enumerate(results):
        ref_text = reference_prefix(reference["regions"][region_i], seconds)
        metrics = compare_text(ref_text, result["effective_text"])
        comparisons.append(metrics)
        result["comparison"] = metrics

    scorable = [
        (r, c) for r, c in zip(results, comparisons) if c["scorable"]
    ]
    passed = sum(r["ok"] and c["pass"] for r, c in scorable)
    return {
        "seconds": seconds,
        "results": results,
        "comparisons": comparisons,
        "passed": passed,
        "total": len(scorable),
        "jobs": len(results),
        "wave_elapsed_sec": round(elapsed, 3),
        "modes": mode_counts(results),
    }


async def main():
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL_ID,
        "sdk_target_version": SDK_VERSION,
        "concurrency": CONCURRENCY,
        "candidate_chunk_secs": list(CANDIDATE_CHUNK_SECS),
    }

    print("Gemini 3.5 Transcribe Live - chunk integrity benchmark")
    print(f"No prompts: GitHub Actions secret + repository audio | language hints={LANGUAGE_CODES}.")

    api_key = load_key()
    src = download_test_audio()
    install_sdk()

    pcm = TMP / "test_audio.pcm"
    client = None

    try:
        duration = await asyncio.to_thread(ffmpeg_to_pcm, src, pcm)
        report["source_duration_sec"] = duration
        report["google_genai_version"] = installed_sdk
        regions = select_regions(pcm, duration)
        report["regions"] = regions

        print(
            f"\n[0] Audio: {duration/60:.2f} min | "
            f"selected region starts: {', '.join(f'{x:.1f}s' for x in regions)}"
        )

        client = genai.Client(api_key=api_key)

        reference = await build_reference(client, pcm, regions)
        report["reference"] = reference

        if not reference["ok"]:
            print(
                f"\nSTOP: only {reference['scorable_regions']} regions had enough "
                "reference speech for a meaningful completeness test."
            )
            report["validated_chunk_sec"] = None
            return report

        if reference["bad_count"]:
            print(
                f"  note: {reference['bad_count']} 10s reference unit(s) were empty "
                "after retry; they are treated as unscorable, not as benchmark failure."
            )

        fallback_count = sum(
            r["mode"] == "interim-fallback" for r in reference["results"]
        )
        print(
            f"  reference complete: {len(reference['results'])} units | "
            f"interim fallbacks={fallback_count}"
        )

        print("\n[2] Candidate chunk-size completeness at c=6")
        print("  size | coverage | wave time | modes | median length | median ref-recall")

        candidate_reports = []
        selected = None

        for seconds in CANDIDATE_CHUNK_SECS:
            candidate = await test_candidate_size(client, pcm, reference, seconds)
            candidate_reports.append(candidate)

            scored = [c for c in candidate["comparisons"] if c["scorable"]]
            lengths = sorted(c["length_ratio"] for c in scored)
            recalls = sorted(c["reference_recall"] for c in scored)
            mid = len(lengths) // 2
            med_len = lengths[mid] if lengths else 0.0
            med_recall = recalls[mid] if recalls else 0.0

            print(
                f"  {seconds:>4}s | {candidate['passed']}/{candidate['total']}     | "
                f"{candidate['wave_elapsed_sec']:>8.1f}s | {candidate['modes']} | "
                f"{med_len:.2f} | {med_recall:.2f}"
            )

            for result in candidate["results"]:
                c = result["comparison"]
                if not c["scorable"]:
                    print(
                        f"       SKIP {result['label']}: reference has only "
                        f"{c['reference_words']} word(s)"
                    )
                elif not (result["ok"] and c["pass"]):
                    print(
                        f"       FAIL {result['label']}: mode={result['mode']} "
                        f"len={c['length_ratio']:.2f} recall={c['reference_recall']:.2f} "
                        f"seq={c['sequence_ratio']:.2f} "
                        f"api={result['api_error'][:80]}"
                    )

            if (
                candidate["total"] >= MIN_SCORABLE_REGIONS
                and candidate["passed"] == candidate["total"]
                and selected is None
            ):
                selected = candidate
                # Candidate sizes are longest -> shortest, so first full pass wins.
                break

        report["candidates"] = candidate_reports

        if selected is None:
            selected_sec = REFERENCE_UNIT_SEC
            # Use measured reference wave time as the conservative fallback estimate.
            ref_elapsed = max(r["elapsed_sec"] for r in reference["results"][:CONCURRENCY])
            selected_wave_elapsed = ref_elapsed
            print(
                f"\nRESULT: no larger candidate fully matched the reference. "
                f"Use {selected_sec}s independent sessions."
            )
        else:
            selected_sec = selected["seconds"]
            selected_wave_elapsed = selected["wave_elapsed_sec"]
            print(f"\nRESULT: validated chunk size = {selected_sec}s at concurrency {CONCURRENCY}")

        chunks = math.ceil(duration / selected_sec)
        waves = math.ceil(chunks / CONCURRENCY)
        estimate_min = waves * selected_wave_elapsed / 60.0
        ideal_floor_min = duration / CONCURRENCY / 60.0

        report["validated_chunk_sec"] = selected_sec
        report["production_estimate"] = {
            "chunks": chunks,
            "waves": waves,
            "measured_wave_sec": round(selected_wave_elapsed, 3),
            "estimated_wall_clock_min": round(estimate_min, 3),
            "ideal_stream_floor_min": round(ideal_floor_min, 3),
        }

        print(
            f"  full recording: {chunks} chunks / {waves} waves | "
            f"measured estimate ~{estimate_min:.2f} min | "
            f"ideal audio floor {ideal_floor_min:.2f} min"
        )
        return report

    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        try:
            pcm.unlink(missing_ok=True)
        except Exception:
            pass



FULL_CHUNK_SEC = 10.0
FULL_CONCURRENCY = 6
FULL_RECOVERY_ATTEMPTS = 2
SUSPICIOUS_MIN_RMS = 140.0
SUSPICIOUS_MAX_WORDS = 1


async def recover_chunk(client, pcm_path, result):
    best = result
    for extra in range(FULL_RECOVERY_ATTEMPTS):
        if best["ok"] and best["effective_words"] > SUSPICIOUS_MAX_WORDS:
            break
        retry = await one_segment(
            client,
            pcm_path,
            result["start_sec"],
            result["audio_sec"],
            result["label"],
            attempt=result["attempt"] + extra + 1,
        )
        if retry["effective_words"] > best["effective_words"]:
            best = retry
        await asyncio.sleep(0.5)
    return best


async def full_e2e_main():
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL_ID,
        "chunk_sec": FULL_CHUNK_SEC,
        "concurrency": FULL_CONCURRENCY,
    }

    print("Gemini 3.5 Transcribe Live - full recording E2E")
    print(
        f"Independent {FULL_CHUNK_SEC:.0f}s manual-VAD sessions | "
        f"concurrency={FULL_CONCURRENCY} | Arabic+English hints."
    )

    api_key = load_key()
    src = download_test_audio()
    install_sdk()

    pcm = TMP / "full_e2e_audio.pcm"
    client = None
    try:
        duration = await asyncio.to_thread(ffmpeg_to_pcm, src, pcm)
        report["source_duration_sec"] = duration
        report["google_genai_version"] = installed_sdk

        jobs = []
        count = math.ceil(duration / FULL_CHUNK_SEC)
        for i in range(count):
            start = i * FULL_CHUNK_SEC
            seconds = min(FULL_CHUNK_SEC, duration - start)
            jobs.append((start, seconds, f"chunk-{i+1:03d}"))

        print(
            f"[plan] duration={duration/60:.2f} min | chunks={len(jobs)} | "
            f"waves={math.ceil(len(jobs)/FULL_CONCURRENCY)}"
        )

        client = genai.Client(api_key=api_key)
        all_results = []
        started = time.monotonic()

        for offset in range(0, len(jobs), FULL_CONCURRENCY):
            wave_jobs = jobs[offset:offset + FULL_CONCURRENCY]
            results, elapsed = await run_wave(client, pcm, wave_jobs)

            recovered = []
            for result in results:
                rms = pcm_rms(
                    pcm,
                    result["start_sec"],
                    min(result["audio_sec"], FULL_CHUNK_SEC),
                )
                result["rms"] = round(rms, 1)
                suspicious = (
                    rms >= SUSPICIOUS_MIN_RMS
                    and result["effective_words"] <= SUSPICIOUS_MAX_WORDS
                )
                if suspicious:
                    result = await recover_chunk(client, pcm, result)
                    result["rms"] = round(rms, 1)
                    result["recovered"] = True
                else:
                    result["recovered"] = False
                recovered.append(result)

            all_results.extend(recovered)
            done = len(all_results)
            usable = sum(r["ok"] for r in recovered)
            print(
                f"  wave {offset//FULL_CONCURRENCY + 1:02d}: "
                f"{usable}/{len(recovered)} usable | {elapsed:.1f}s | "
                f"done {done}/{len(jobs)} | modes={mode_counts(recovered)}"
            )

        wall = time.monotonic() - started

        blocking = [
            r for r in all_results
            if r.get("rms", 0) >= SUSPICIOUS_MIN_RMS
            and not r["effective_text"].strip()
        ]
        suspicious_short = [
            r for r in all_results
            if r.get("rms", 0) >= SUSPICIOUS_MIN_RMS
            and r["effective_words"] <= SUSPICIOUS_MAX_WORDS
        ]

        transcript = "\n".join(
            r["effective_text"].strip()
            for r in all_results
            if r["effective_text"].strip()
        ).strip()

        transcript_path = Path(
            os.environ.get("FULL_TRANSCRIPT_PATH", "gemini35_live_full_transcript.txt")
        )
        transcript_path.write_text(transcript, encoding="utf-8")

        report.update({
            "wall_clock_sec": round(wall, 3),
            "wall_clock_min": round(wall/60, 3),
            "chunks_total": len(all_results),
            "chunks_usable": sum(r["ok"] for r in all_results),
            "blocking_empty_non_silent": len(blocking),
            "suspicious_short_non_silent": len(suspicious_short),
            "mode_counts": mode_counts(all_results),
            "total_words": len(normalize_tokens(transcript)),
            "transcript_path": str(transcript_path),
            "chunks": all_results,
        })

        print("\n[result]")
        print(f"  wall clock: {wall/60:.2f} min")
        print(
            f"  usable chunks: {report['chunks_usable']}/{report['chunks_total']} | "
            f"blocking non-silent empty: {len(blocking)} | "
            f"suspicious short: {len(suspicious_short)}"
        )
        print(f"  transcript words: {report['total_words']}")
        print(f"  modes: {report['mode_counts']}")
        print(f"  transcript: {transcript_path}")

        report["validated"] = len(blocking) == 0
        return report
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        try:
            pcm.unlink(missing_ok=True)
        except Exception:
            pass


report = asyncio.run(full_e2e_main())
report_path = Path(
    os.environ.get("FULL_REPORT_PATH", "gemini35_live_full_e2e_report.json")
)
report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Saved report: {report_path}")

summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
if summary_path:
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("## Gemini 3.5 Live full E2E\n\n")
        fh.write(f"- Validated: {report.get('validated')}\n")
        fh.write(f"- Wall clock: {report.get('wall_clock_min')} min\n")
        fh.write(
            f"- Usable chunks: {report.get('chunks_usable')}/"
            f"{report.get('chunks_total')}\n"
        )
        fh.write(
            f"- Blocking non-silent empty chunks: "
            f"{report.get('blocking_empty_non_silent')}\n"
        )
        fh.write(f"- Transcript words: {report.get('total_words')}\n")
