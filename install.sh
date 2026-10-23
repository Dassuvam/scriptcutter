#!/usr/bin/env bash
# ScriptCutter setup for macOS. Everything it installs is free.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS="$HOME/.cache/whisper-models"
HF="https://huggingface.co"
RESOLVE_SCRIPTS="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

download() {  # file url
  if [ -s "$MODELS/$1" ]; then
    echo "  $1 already installed"
  else
    echo "  downloading $1 ..."
    curl -L --fail --progress-bar -o "$MODELS/$1.part" "$2" && mv "$MODELS/$1.part" "$MODELS/$1"
  fi
}

echo "ScriptCutter $(cat "$DIR/VERSION") setup"

step "1/5  Apple developer tools"
if ! xcode-select -p >/dev/null 2>&1; then
  echo "  macOS will now ask to install the Command Line Tools. Click Install, wait, then run this installer again."
  xcode-select --install || true
  exit 1
fi
echo "  ok"

step "2/5  Homebrew, ffmpeg and whisper-cpp"
if ! command -v brew >/dev/null; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi
if ! command -v brew >/dev/null; then
  echo "  Homebrew is not installed. Installing it (you may be asked for your Mac password)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
fi
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v whisper-cli >/dev/null || brew install whisper-cpp
echo "  ok"

step "3/5  Speech models (saved in ~/.cache/whisper-models)"
mkdir -p "$MODELS"
download ggml-small.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
download ggml-silero-v6.2.0.bin "$HF/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin"
echo
echo "  Optional bigger models give better results but run slower:"
echo "    large-v3       2.9 GB  best for Hindi, Hinglish and other non-English speech"
echo "    large-v3-turbo 1.5 GB  best for English"
read -r -p "  Download large-v3? [y/N] " A
[[ "$A" =~ ^[Yy]$ ]] && download ggml-large-v3.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"
read -r -p "  Download large-v3-turbo? [y/N] " B
[[ "$B" =~ ^[Yy]$ ]] && download ggml-large-v3-turbo.bin "$HF/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"

step "4/5  DaVinci Resolve menu"
mkdir -p "$RESOLVE_SCRIPTS"
sed "s|__LAUNCHER_PATH__|$DIR/launcher.py|" "$DIR/resolve/ScriptCutterV2.lua" > "$RESOLVE_SCRIPTS/ScriptCutterV2.lua"
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
echo "  Added Workspace > Scripts > ScriptCutterV2 (restart Resolve once)."
echo "  In Resolve: Preferences > System > General > External scripting using = Local."

step "5/5  Self-test"
if /usr/bin/python3 "$DIR/tests/test_v2.py" >/dev/null 2>&1; then
  echo "  all tests passed"
else
  echo "  some tests failed - details: python3 tests/test_v2.py"
fi

echo
echo "Done. Open ScriptCutter from Resolve (Workspace > Scripts > ScriptCutterV2)"
echo "or double-click 'Open ScriptCutter.command'."
