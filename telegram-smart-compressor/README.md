# Telegram Smart Compressor

[Open Stable in Colab](https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/telegram-smart-compressor/launcher.ipynb)

[Open Beta in Colab](https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/beta/telegram-smart-compressor/launcher-beta.ipynb)

One-click Google Colab workflow for compressing Telegram audio and educational videos.

## UX

After the first setup, the normal workflow is:

1. Send audio/video to the private **📦 Smart Compressor** channel.
2. Open the same Colab notebook.
3. Press **▶ Run**.

The engine processes all new files automatically and posts the results back to the same channel.

## First run

The engine asks once for your own Telegram `API_ID`, `API_HASH`, and phone number, then signs in to your Telegram account and stores the session/config in **your own Google Drive**.

It then creates (or reuses) a private **📦 Smart Compressor** channel, posts instructions, pins the instruction message, and tries to pin the channel in your chat list.

No personal credentials or Telegram sessions are stored in this GitHub repository.

Get Telegram API credentials from: https://my.telegram.org/apps

## Profiles

- `SMART_AUTO` — default; tiny Opus for audio, balanced lecture compression for video.
- `AUDIO_TINY` — turn audio or video into very small Opus audio.
- `VIDEO_BALANCED` — good default for educational video.
- `VIDEO_FAST` — prioritizes speed and uses NVENC automatically when available.
- `VIDEO_SMALLEST` — prioritizes final size.
- `VIDEO_TARGET_SIZE` — target an approximate final size; leave the target blank for 100 MB.

## Re-use an old result

Generated results are ignored on future normal runs, so there is no compression loop.

To process an old result again, reply to that Telegram message with:

- `🔁` — process again using the current profile.
- `🔁 أصغر` — stronger video compression.
- `🔁 صوت` — convert/extract to tiny audio.
- `🔁 80` — target about 80 MB.

## Updates

- `main` = stable channel for friends/users.
- `beta` = testing channel for new changes.

Launchers resolve the latest branch commit first and download the engine from that immutable commit, avoiding stale GitHub raw-cache issues.

## Privacy

Each user's credentials, session, workspace channel ID, and processed-file state live only in that user's Google Drive under:

`MyDrive/Telegram_Extreme_Compressor`

Do not share that Drive folder or Telegram session file.
