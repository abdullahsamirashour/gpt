"""Arabic ASR benchmark engine for Google Colab.

The Colab launcher downloads this file from GitHub at runtime. Keep user-facing
output compact; persist rich diagnostics separately for debugging.
"""

from __future__ import annotations

import base64
import contextlib
import gc
import hashlib
import html
import importlib.metadata
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ENGINE_VERSION = "1.1.0"
PACKAGES = [
    "qwen-asr==0.0.6",
    "transformers==4.57.6",
    "gdown>=5.2.0",
    "librosa>=0.10.2",
    "soundfile>=0.12.1",
    "accelerate>=1.0.0",
]
SAMPLE_RATE = 16000
CHUNK_SECONDS = 30
MAX_NEW_TOKENS = 256
MODELS = [
    ("Audar-ASR-V1-Turbo", "audarai/Audar-ASR-V1-Turbo"),
    ("QwenCleo-ASR", "mohammedaly22/QwenCleo-ASR"),
]
LOG_REPO = "abdullahsamirashour/ai-cli-colab-lab"
LOG_PREFIX = "arabic-asr-benchmark/logs"
LOG_SECRET = "ASR_GITHUB_TOKEN"
LOG_CAPTURE_LIMIT = 50000

# Keep the notebook quiet. We capture model-library stderr ourselves.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pkg_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sanitize_text(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    if len(text) <= limit * 2:
        return text
    return text[:limit] + "\n…\n" + text[-limit:]


def exception_info(exc: BaseException) -> dict:
    return {
        "type": type(exc).__name__,
        "message": str(exc)[:4000],
        "traceback": traceback.format_exc()[-20000:],
    }


def install_dependencies(log: dict) -> None:
    print("📦 Preparing environment...")
    started = time.time()
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-q",
        "--disable-pip-version-check",
        *PACKAGES,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log["setup"] = {
        "seconds": round(time.time() - started, 3),
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-12000:],
    }
    if proc.returncode != 0:
        raise RuntimeError("Dependency installation failed: " + proc.stderr[-1500:])
    print("✅ Environment ready")


def github_log_token() -> str | None:
    try:
        from google.colab import userdata

        token = userdata.get(LOG_SECRET)
        return str(token).strip() if token else None
    except Exception:
        return None


def sync_log_to_github(log: dict) -> dict:
    token = github_log_token()
    if not token:
        return {"status": "not_configured", "secret": LOG_SECRET}

    run_id = log["run_id"]
    day = run_id[:10]
    path = f"{LOG_PREFIX}/{day}/{run_id}.json"
    payload = json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8")
    body = json.dumps(
        {
            "message": f"Add Arabic ASR diagnostic log {run_id}",
            "content": base64.b64encode(payload).decode("ascii"),
            "branch": "main",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{LOG_REPO}/contents/{path}",
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Arabic-ASR-Colab",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {
            "status": "uploaded",
            "repo": LOG_REPO,
            "path": path,
            "commit_sha": data.get("commit", {}).get("sha"),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "repo": LOG_REPO,
            "path": path,
            "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
        }


def write_local_log(log: dict, path: Path) -> None:
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    drive_link = str(globals().get("drive_link", "")).strip()
    if not drive_link:
        raise ValueError("❌ حط رابط Google Drive الأول.")

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    work = Path("/content/asr_compare")
    results_dir = Path("/content/asr_results")
    for folder in (work, results_dir):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)

    log_path = results_dir / "diagnostic_log.json"
    log = {
        "run_id": run_id,
        "started_at": utc_now(),
        "engine": {
            "version": ENGINE_VERSION,
            "commit": str(globals().get("ASR_ENGINE_SHA", "unknown")),
        },
        "privacy": {
            "google_drive_link_stored": False,
            "full_transcripts_stored_in_github_log": False,
            "transcript_preview_chars_each_end": 800,
        },
        "config": {
            "sample_rate": SAMPLE_RATE,
            "chunk_seconds": CHUNK_SECONDS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "models": [model_id for _, model_id in MODELS],
        },
        "models": {},
    }

    overall_started = time.time()
    fatal_exc = None
    zip_path = None

    try:
        install_dependencies(log)

        import gdown
        import librosa
        import torch
        import transformers
        from IPython.display import HTML, display
        from google.colab import files
        from qwen_asr import Qwen3ASRModel
        from qwen_asr.inference.utils import split_audio_into_chunks

        transformers.utils.logging.set_verbosity_error()

        if not torch.cuda.is_available():
            raise RuntimeError(
                "❌ لازم GPU. من Runtime → Change runtime type → GPU ثم شغّل الخلية تاني."
            )

        major, minor = torch.cuda.get_device_capability(0)
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)

        # Both checkpoints are published/recommended in BF16. T4/Turing has no
        # native BF16, and forcing them to FP16 can create mixed-dtype projector
        # failures. Use FP32 as the compatibility path on pre-Ampere GPUs.
        if major >= 8:
            dtype = torch.bfloat16
            precision_mode = "bf16"
        else:
            dtype = torch.float32
            precision_mode = "fp32_compat"

        log["runtime"] = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "qwen_asr": pkg_version("qwen-asr"),
            "cuda_runtime": torch.version.cuda,
            "gpu": gpu_name,
            "gpu_compute_capability": f"{major}.{minor}",
            "gpu_total_gb": round(gpu_mem_gb, 2),
            "precision_mode": precision_mode,
            "dtype": str(dtype),
        }

        print(f"🎮 {gpu_name} | {precision_mode}")
        if precision_mode == "fp32_compat":
            print("ℹ️ T4 compatibility mode: reliable FP32 instead of unsupported BF16/unsafe FP16")

        print("\n⬇️ Downloading recording...")
        input_path = gdown.download(
            url=drive_link,
            output=str(work / "input_media"),
            quiet=True,
            fuzzy=True,
        )
        if not input_path or not Path(input_path).exists():
            raise RuntimeError(
                "❌ لم أستطع تنزيل الملف. اجعل General access = Anyone with the link → Viewer ثم جرّب تاني."
            )
        input_path = Path(input_path)

        log["source"] = {
            "size_bytes": input_path.stat().st_size,
            "sha256": sha256_file(input_path),
        }

        wav_path = work / "input_16k_mono.wav"
        print("🎧 Preparing audio...")
        ffmpeg_started = time.time()
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(input_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            capture_output=True,
            text=True,
        )
        log["audio_prepare"] = {
            "seconds": round(time.time() - ffmpeg_started, 3),
            "ffmpeg_stderr": proc.stderr[-8000:],
        }
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg failed: " + proc.stderr[-1500:])

        wav, _ = librosa.load(wav_path, sr=SAMPLE_RATE, mono=True)
        parts = split_audio_into_chunks(wav, SAMPLE_RATE, max_chunk_sec=CHUNK_SECONDS)
        duration_sec = len(wav) / SAMPLE_RATE
        log["source"].update(
            {
                "duration_seconds": round(duration_sec, 3),
                "chunks": len(parts),
            }
        )
        print(f"✅ Audio: {duration_sec / 60:.1f} min | {len(parts)} chunks")

        def unload(model=None) -> None:
            if model is not None:
                del model
            gc.collect()
            torch.cuda.empty_cache()

        def transcribe_model(label: str, model_id: str):
            model_log = {
                "model_id": model_id,
                "requested_dtype": str(dtype),
                "status": "started",
                "started_at": utc_now(),
                "chunks": [],
            }
            log["models"][label] = model_log
            started = time.time()
            model = None
            captured = io.StringIO()

            try:
                torch.cuda.reset_peak_memory_stats()
                print(f"\n🤖 {label}")
                print("   Loading model...")
                load_started = time.time()
                with contextlib.redirect_stderr(captured):
                    load_kwargs = {
                        "dtype": dtype,
                        "device_map": "cuda:0",
                        "low_cpu_mem_usage": True,
                        "max_inference_batch_size": 1,
                        "max_new_tokens": MAX_NEW_TOKENS,
                    }
                    if major < 8:
                        load_kwargs["attn_implementation"] = "sdpa"
                    model = Qwen3ASRModel.from_pretrained(model_id, **load_kwargs)
                model_log["load_seconds"] = round(time.time() - load_started, 3)
                model_log["actual_model_dtype"] = str(getattr(model, "dtype", "unknown"))

                # QwenCleo ships temperature while do_sample=false; clear it to
                # avoid a harmless generation warning in every chunk.
                inner_model = getattr(model, "model", None)
                generation_config = getattr(inner_model, "generation_config", None)
                if generation_config is not None and not getattr(generation_config, "do_sample", False):
                    generation_config.temperature = None

                texts = []
                total = len(parts)
                infer_started = time.time()
                for i, (chunk, offset_sec) in enumerate(parts, 1):
                    chunk_started = time.time()
                    with contextlib.redirect_stderr(captured):
                        result = model.transcribe(
                            audio=(chunk, SAMPLE_RATE),
                            language="Arabic",
                            return_time_stamps=False,
                        )[0]
                    text = (result.text or "").strip()
                    if text:
                        texts.append(text)
                    model_log["chunks"].append(
                        {
                            "index": i,
                            "offset_seconds": round(float(offset_sec), 3),
                            "duration_seconds": round(len(chunk) / SAMPLE_RATE, 3),
                            "elapsed_seconds": round(time.time() - chunk_started, 3),
                            "output_chars": len(text),
                        }
                    )
                    print(f"   {i}/{total} chunks", end="\r")

                print(" " * 60, end="\r")
                transcript = "\n".join(texts).strip()
                elapsed = time.time() - started
                model_log.update(
                    {
                        "status": "success",
                        "inference_seconds": round(time.time() - infer_started, 3),
                        "total_seconds": round(elapsed, 3),
                        "output_chars": len(transcript),
                        "output_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
                        "output_preview": sanitize_text(transcript),
                        "gpu_peak_allocated_gb": round(torch.cuda.max_memory_allocated() / (1024**3), 3),
                        "gpu_peak_reserved_gb": round(torch.cuda.max_memory_reserved() / (1024**3), 3),
                    }
                )
                print(f"✅ {label}: {elapsed / 60:.1f} min")
                return transcript, elapsed, None
            except Exception as exc:
                elapsed = time.time() - started
                model_log.update(
                    {
                        "status": "failed",
                        "total_seconds": round(elapsed, 3),
                        "error": exception_info(exc),
                        "gpu_peak_allocated_gb": round(torch.cuda.max_memory_allocated() / (1024**3), 3),
                        "gpu_peak_reserved_gb": round(torch.cuda.max_memory_reserved() / (1024**3), 3),
                    }
                )
                print(f"❌ {label} failed: {type(exc).__name__}: {str(exc)[:220]}")
                return "", elapsed, repr(exc)
            finally:
                model_log["library_stderr_tail"] = captured.getvalue()[-LOG_CAPTURE_LIMIT:]
                model_log["ended_at"] = utc_now()
                unload(model)

        results = {}
        for label, model_id in MODELS:
            text, elapsed, error = transcribe_model(label, model_id)
            results[label] = {"text": text, "elapsed": elapsed, "error": error}
            if text:
                (results_dir / f"{label}.txt").write_text(text, encoding="utf-8")

        report = [
            "# Arabic ASR comparison",
            "",
            f"- Engine version: {ENGINE_VERSION}",
            f"- Engine commit: {log['engine']['commit'][:10]}",
            f"- Source duration: {duration_sec / 60:.1f} minutes",
            f"- GPU: {gpu_name}",
            f"- Precision: {precision_mode}",
            f"- Chunking: smart low-energy boundaries, max {CHUNK_SECONDS}s",
            "",
        ]
        for label, _ in MODELS:
            item = results[label]
            report += [f"## {label}", f"Runtime: {item['elapsed'] / 60:.1f} min", ""]
            report += [item["text"] if item["text"] else f"FAILED: {item['error']}", ""]
        (results_dir / "comparison.md").write_text("\n".join(report), encoding="utf-8")

        def card(label: str, item: dict) -> str:
            if item["text"]:
                preview = item["text"][:5000]
                if len(item["text"]) > 5000:
                    preview += "\n\n… [Preview truncated — full text is in the ZIP]"
            else:
                preview = "FAILED: " + str(item["error"])
            return f"""
            <div style='border:1px solid #ddd;border-radius:14px;padding:16px;margin:12px 0;background:#fff'>
              <div style='font-size:18px;font-weight:700;margin-bottom:8px'>{html.escape(label)}</div>
              <div style='font-size:13px;color:#666;margin-bottom:12px'>Runtime: {item['elapsed'] / 60:.1f} min</div>
              <pre dir='auto' style='white-space:pre-wrap;font-family:Arial,sans-serif;line-height:1.65;font-size:15px;margin:0'>{html.escape(preview)}</pre>
            </div>
            """

        display(HTML("<h3>النتيجة</h3>" + "".join(card(label, results[label]) for label, _ in MODELS)))

        log["status"] = "success" if all(v["text"] for v in results.values()) else "partial_failure"
        zip_path = "/content/Arabic_ASR_comparison.zip"

    except Exception as exc:
        fatal_exc = exc
        log["status"] = "fatal_error"
        log["fatal_error"] = exception_info(exc)
        print(f"\n❌ Run failed: {type(exc).__name__}: {str(exc)[:300]}")

    finally:
        log["ended_at"] = utc_now()
        log["total_seconds"] = round(time.time() - overall_started, 3)
        write_local_log(log, log_path)
        sync_result = sync_log_to_github(log)
        log["github_log_sync"] = sync_result
        # Rewrite local copy so it also records sync status.
        write_local_log(log, log_path)

        if sync_result["status"] == "uploaded":
            print("🧾 Diagnostic log synced privately to GitHub")
        elif sync_result["status"] == "not_configured":
            print(f"🧾 Diagnostic log saved locally (GitHub sync needs Colab secret: {LOG_SECRET})")
        else:
            print("⚠️ Diagnostic log saved locally; GitHub sync failed")

    if fatal_exc is not None:
        raise fatal_exc

    # Create the ZIP only after the final log has been written.
    zip_path = shutil.make_archive("/content/Arabic_ASR_comparison", "zip", results_dir)
    print("📦 Downloading results...")
    files.download(zip_path)


if __name__ == "__main__" or "google.colab" in sys.modules:
    main()
