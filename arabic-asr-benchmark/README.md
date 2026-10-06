# Arabic ASR Benchmark — Audar vs QwenCleo

Google Colab benchmark for the same Arabic recording on:

1. \`audarai/Audar-ASR-V1-Turbo\`
2. \`mohammedaly22/QwenCleo-ASR\`

## Open in Colab

https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/arabic-asr-benchmark/launcher.ipynb

## Design

\`launcher.ipynb\` is intentionally tiny and stable. On every run it resolves the latest \`main\` commit, downloads \`engine.py\` from that exact commit, compiles it, and runs it with the Google Drive link entered in the Colab form.

All functional changes belong in \`engine.py\`. Updating the engine on GitHub is enough; users do not need a new notebook.

## Runtime compatibility

The two checkpoints are BF16-first. On Ampere-or-newer GPUs the engine uses BF16. On pre-Ampere GPUs such as Colab T4, it uses FP32 compatibility mode instead of forcing FP16, which can produce mixed-dtype failures in the audio path.

The benchmark uses the same 30-second low-energy chunk boundaries for both models and \`max_new_tokens=256\`.

## Private diagnostics

Every run writes \`diagnostic_log.json\` into the downloaded results ZIP. The log contains runtime/package versions, GPU details, source hash/duration (not the Google Drive URL), per-model load/inference timing, per-chunk timings, peak GPU memory, compact transcript previews, captured library warnings, and full exception tracebacks.

Optional automatic log sync goes to the private repository \`abdullahsamirashour/ai-cli-colab-lab\` under \`arabic-asr-benchmark/logs/\`.

For automatic sync, add one Colab secret named \`ASR_GITHUB_TOKEN\`. Use a fine-grained GitHub PAT restricted to the private \`ai-cli-colab-lab\` repository with **Contents: Read and write**. The token is read only from Colab Secrets and is never printed or stored in the log.

Full transcripts and the Google Drive URL are never uploaded in the diagnostic log.
