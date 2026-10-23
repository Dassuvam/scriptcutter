#!/usr/bin/env bash
# ScriptCutter v2 setup (macOS). Everything installed here is free.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS="$HOME/.cache/whisper-models"
HF="https://huggingface.co"
mkdir -p "$MODELS"

echo "== ScriptCutter v2 setup =="
command -v python3 >/dev/null || { echo "Python 3 is required (xcode-select --install)."; exit 1; }

if ! command -v brew >/dev/null; then
  echo "Homebrew is needed for ffmpeg and whisper-cpp: https://brew.sh"; exit 1
fi
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v whisper-cli >/dev/null || brew install whisper-cpp

get() {  # name url
  if [ ! -s "$MODELS/$1" ]; then
    echo "  downloading $1 ..."
    curl -L --fail -o "$MODELS/$1.part" "$2" && mv "$MODELS/$1.part" "$MODELS/$1"
  else
    echo "  $1 already present"
  fi
}

echo "[models]"
# small (466 MB): good speed/quality, can translate Hindi etc. to English for analysis
get ggml-small.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
# Silero voice detector (885 KB): stops typing/music/noise being "transcribed"
get ggml-silero-v6.2.0.bin "$HF/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin"

read -r -p "Also download the medium model (1.5 GB, better accuracy, ~2x slower)? [y/N] " A
[[ "$A" =~ ^[Yy]$ ]] && get ggml-medium.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin"
read -r -p "Also download large-v3-turbo (1.6 GB, best for English/original-language transcripts)? [y/N] " B
[[ "$B" =~ ^[Yy]$ ]] && get ggml-large-v3-turbo.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"

echo "[resolve menu]"
DEST="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
mkdir -p "$DEST"
sed "s|__LAUNCHER_PATH__|$DIR/launcher.py|" "$DIR/scripts/ScriptCutterV2.lua" > "$DEST/ScriptCutterV2.lua"
echo "  Workspace > Scripts > ScriptCutterV2 (restart Resolve once)"
echo "  Resolve: Preferences > System > General > External scripting using = Local"

echo "[tests]"
python3 "$DIR/tests/test_v2.py" >/dev/null 2>&1 && echo "  all tests passed" || echo "  some tests failed - run: python3 tests/test_v2.py"
echo "Done. Start with ./start.sh or from the Resolve menu."
