# SillyTavern Telegram Bridge v0.2.012

Release date: 2026-09-18

## Highlights

- Removed the unused `clear_native_file_cache()` function.
- Removed the redundant Live API Sync JSONL serialization/parsing round trip.
- Live API chat records are now validated and normalized directly.
- Preserved bounded message/transcript limits, metadata handling, and swipe variants.
- Updated README, interactive Help, `.env.example`, and provider configuration guidance.

## Compatibility

Live API Sync remains opt-in and uses the local SillyTavern API only. The bridge
no longer documents or maintains chat-file polling or manual JSONL transcript
transfer as synchronization paths.

## Verification

- Full repository unittest suite: 182 tests passed.
- Python compilation: passed.
- Runtime loader import and staged module load: passed.
- Removed-symbol scan: zero matches.
- `git diff --check`: passed.
