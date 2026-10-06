"""Arabic ASR benchmark engine for Google Colab.

This file is intentionally kept on GitHub. The Colab launcher downloads the
latest committed version at runtime, so behavior can be updated without
re-uploading the notebook.
"""

from __future__ import annotations

import gc
import html
import shutil
import subprocess
import sys
import time
from pathlib import Path


ENGINE_VERSION = "1.0.0"
PACKAGES = [
    "qwen-asr==0.0.6",
    "gdown>=5.2.0",
    "librosa>=0.10.2",
    "soundfile>=0.12.1",
    "accelerate>=1.0.0",
]
CHUNK_SECONDS = 60
SAMPLE_RATE = 16000
MAX_NEW_TOKENS = 768
MODELS = [
    ("Audar-ASR-V1-Turbo", "audarai/Audar-ASR-V1-Turbo"),
    ("QwenCleo-ASR", "mohammedaly22/QwenCleo-ASR"),
]


def install_dependencies() -> None:
    print("📦 Preparing environment...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", *PACKAGES]
    )


def main() -> None:
    drive_link = str(globals().get("drive_link", "")).strip()
    if not drive_link:
        raise ValueError("❌ حط رابط Google Drive الأول.")

    install_dependencies()

    import gdown
    import librosa
    import torch
    from IPython.display import HTML, display
    from google.colab import files
    from qwen_asr import Qwen3ASRModel
    from qwen_asr.inference.utils import split_audio_into_chunks

    if not torch.cuda.is_available():
        raise RuntimeError(
            "❌ لازم GPU. من Runtime → Change runtime type → GPU ثم شغّل الخلية تاني."
        )

    major, _ = torch.cuda.get_device_capability(0)
    dtype = torch.bfloat16 if major >= 8 else torch.float16
    gpu_name = torch.cuda.get_device_name(0)
    engine_sha = str(globals().get("ASR_ENGINE_SHA", "unknown"))[:10]
    print(f"✅ Engine {ENGINE_VERSION} | commit {engine_sha}")
    print(f"🎮 GPU: {gpu_name} | dtype: {dtype}")

    work = Path("/content/asr_compare")
    results_dir = Path("/content/asr_results")
    for folder in (work, results_dir):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)

    print("\n⬇️ Downloading recording from Google Drive...")
    input_path = gdown.download(
        url=drive_link,
        output=str(work / "input_media"),
        quiet=False,
        fuzzy=True,
    )
    if not input_path or not Path(input_path).exists():
        raise RuntimeError(
            "❌ لم أستطع تنزيل الملف. اجعل General access = Anyone with the link → Viewer ثم جرّب تاني."
        )
    input_path = Path(input_path)

    wav_path = work / "input_16k_mono.wav"
    print("\n🎧 Preparing audio...")
    subprocess.run(
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
        check=True,
    )

    wav, _ = librosa.load(wav_path, sr=SAMPLE_RATE, mono=True)
    parts = split_audio_into_chunks(wav, SAMPLE_RATE, max_chunk_sec=CHUNK_SECONDS)
    duration_min = len(wav) / SAMPLE_RATE / 60
    print(f"✅ Audio: {duration_min:.1f} min | {len(parts)} smart chunks")

    def unload(model=None) -> None:
        if model is not None:
            del model
        gc.collect()
        torch.cuda.empty_cache()

    def transcribe_model(label: str, model_id: str):
        print(f"\n{'=' * 60}\n🤖 {label}\n{'=' * 60}")
        started = time.time()
        model = None
        try:
            model = Qwen3ASRModel.from_pretrained(
                model_id,
                dtype=dtype,
                device_map="cuda:0",
                max_inference_batch_size=1,
                max_new_tokens=MAX_NEW_TOKENS,
            )

            texts = []
            total = len(parts)
            for i, (chunk, offset_sec) in enumerate(parts, 1):
                print(f"  [{i:>3}/{total}] {offset_sec / 60:6.1f} min", end="\r")
                result = model.transcribe(
                    audio=(chunk, SAMPLE_RATE),
                    language="Arabic",
                    return_time_stamps=False,
                )[0]
                text = (result.text or "").strip()
                if text:
                    texts.append(text)

            transcript = "\n".join(texts).strip()
            elapsed = time.time() - started
            print(" " * 80, end="\r")
            print(f"✅ {label} finished in {elapsed / 60:.1f} min")
            return transcript, elapsed, None
        except Exception as exc:
            elapsed = time.time() - started
            print(f"❌ {label} failed: {exc}")
            return "", elapsed, repr(exc)
        finally:
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
        f"- Engine commit: {engine_sha}",
        f"- Source duration: {duration_min:.1f} minutes",
        f"- GPU: {gpu_name}",
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
            preview = item["text"][:6000]
            if len(item["text"]) > 6000:
                preview += "\n\n… [Preview truncated — full text is in the downloaded ZIP]"
        else:
            preview = "FAILED: " + str(item["error"])
        return f"""
        <div style='border:1px solid #ddd;border-radius:14px;padding:16px;margin:12px 0;background:#fff'>
          <div style='font-size:18px;font-weight:700;margin-bottom:8px'>{html.escape(label)}</div>
          <div style='font-size:13px;color:#666;margin-bottom:12px'>Runtime: {item['elapsed'] / 60:.1f} min</div>
          <pre dir='auto' style='white-space:pre-wrap;font-family:Arial,sans-serif;line-height:1.65;font-size:15px;margin:0'>{html.escape(preview)}</pre>
        </div>
        """

    print("\n🏁 Done")
    display(
        HTML(
            "<h3>النتيجة</h3>"
            + "".join(card(label, results[label]) for label, _ in MODELS)
        )
    )

    zip_path = shutil.make_archive("/content/Arabic_ASR_comparison", "zip", results_dir)
    print("📦 Downloading full results ZIP...")
    files.download(zip_path)


if __name__ == "__main__" or "google.colab" in sys.modules:
    main()
