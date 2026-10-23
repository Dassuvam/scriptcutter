#!/usr/bin/env bash
# ScriptCutter installer for macOS.
# Installs ffmpeg and whisper.cpp with Homebrew, then runs install.py (models, Resolve menu, self-test).
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
step() { printf '\n== %s\n' "$1"; }

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer is for macOS. On Windows, double-click 'Install ScriptCutter.bat'."
  exit 1
fi

step "Apple developer tools"
if ! xcode-select -p >/dev/null 2>&1; then
  echo "  macOS will ask to install the Command Line Tools. Click Install, wait, then run this installer again."
  xcode-select --install || true
  exit 1
fi
echo "  ok"

step "Homebrew, ffmpeg and whisper.cpp"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if ! command -v brew >/dev/null && [ -x "$b" ]; then eval "$("$b" shellenv)"; fi
done
if ! command -v brew >/dev/null; then
  echo "  Installing Homebrew (it may ask for your Mac password)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; fi
  done
fi
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v whisper-cli >/dev/null || brew install whisper-cpp
echo "  ok"

# files downloaded from the internet are quarantined; the launcher scripts need to run
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true

/usr/bin/python3 "$DIR/install.py" "$@"
