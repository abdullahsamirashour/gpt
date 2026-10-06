# Arabic ASR Benchmark — Audar vs QwenCleo

Google Colab benchmark for the same Arabic recording on:

1. `audarai/Audar-ASR-V1-Turbo`
2. `mohammedaly22/QwenCleo-ASR`

## Open in Colab

https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/arabic-asr-benchmark/launcher.ipynb

## Design

`launcher.ipynb` is intentionally tiny and stable. On every run it:

1. resolves the latest commit on `main`;
2. downloads `engine.py` from that exact commit;
3. compiles it before execution;
4. runs it with the Google Drive link entered in the Colab form.

All functional changes belong in `engine.py`. Updating the engine on GitHub is enough; users do not need a new notebook.

## Usage

1. Open the Colab link above.
2. Use a GPU runtime.
3. Paste a Google Drive file link with `Anyone with the link -> Viewer` access.
4. Run the single cell.
5. The notebook downloads a ZIP containing both transcripts and `comparison.md`.

No API key is required.
