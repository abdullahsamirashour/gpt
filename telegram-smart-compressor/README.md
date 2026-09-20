# Telegram Smart Compressor

GitHub-hosted engine for a permanent Google Colab launcher.

## Architecture
- `engine.py`: current compressor engine. Contains no Telegram credentials or session data.
- `version.json`: current stable engine version.
- The Colab launcher downloads `engine.py` on every run.
- Telegram credentials/session/state remain in the user's Google Drive folder `Telegram_Extreme_Compressor`.

## Update workflow
Future code updates only require editing `engine.py` in this repository. The user's Colab notebook does not need to be replaced.
