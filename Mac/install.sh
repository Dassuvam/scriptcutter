#!/bin/bash
# ScriptCutter installer for macOS.
#
# It runs three ways:
#   * from the download: "Install ScriptCutter.command" starts app/install.sh
#   * from the source code: bash Mac/install.sh
#   * online, without the "downloaded from the internet" warning:
#       curl -fsSL https://github.com/Dassuvam/scriptcutter/releases/latest/download/install-mac.sh | bash
#     It then fetches ScriptCutter-Mac.zip itself.
#
# It installs Apple's command line tools (for Python), Homebrew, ffmpeg and whisper.cpp,
# then runs install.py, which copies ScriptCutter into place, downloads the speech
# models, adds it to DaVinci Resolve and ~/Applications, checks it and opens it.
# Options go on to install.py, for example --yes or --models small,large-v3.
set -e

REPO="Dassuvam/scriptcutter"
step() { printf '\n== %s\n' "$1"; }

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer is for macOS. On Windows, download ScriptCutter-Windows.zip."
  exit 1
fi

# install.py sits next to this file (download), one folder up (source code), or nowhere (online)
APP=""
SELF="${BASH_SOURCE[0]:-}"
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
  HERE="$(cd "$(dirname "$SELF")" && pwd)"
  for d in "$HERE" "$HERE/.."; do
    if [ -f "$d/install.py" ]; then APP="$(cd "$d" && pwd)"; break; fi
  done
fi
if [ -z "$APP" ]; then
  step "Downloading ScriptCutter"
  TMP="$(mktemp -d)"
  curl -fL --progress-bar -o "$TMP/ScriptCutter-Mac.zip" \
    "https://github.com/$REPO/releases/latest/download/ScriptCutter-Mac.zip"
  ditto -x -k "$TMP/ScriptCutter-Mac.zip" "$TMP"
  # piped into bash, stdin is the script itself: give the installer the keyboard back for its questions
  if : </dev/tty 2>/dev/null; then
    exec /bin/bash "$TMP/ScriptCutter/app/install.sh" "$@" </dev/tty
  fi
  exec /bin/bash "$TMP/ScriptCutter/app/install.sh" "$@"
fi

step "Apple command line tools"
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install >/dev/null 2>&1 || true
  echo "  A window asks to install the command line tools: click Install."
  echo "  The installer continues by itself when they are ready (this can take 10 minutes)."
  until xcode-select -p >/dev/null 2>&1; do sleep 10; done
fi
echo "  ok"

step "Homebrew, ffmpeg and whisper.cpp"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do  # Apple Silicon, Intel
  if ! command -v brew >/dev/null && [ -x "$b" ]; then eval "$("$b" shellenv)"; fi
done
if ! command -v brew >/dev/null; then
  echo "  Installing Homebrew. It asks for your Mac password (typing it shows nothing)."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$b" ]; then eval "$("$b" shellenv)"; fi
  done
fi
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v whisper-cli >/dev/null || brew install whisper-cpp  # uses the Mac's graphics chip (Metal)
echo "  ok"

# macOS marks downloaded files; clear the mark on the download so "Uninstall ScriptCutter" opens without a warning
if [ -f "$APP/../Install ScriptCutter.command" ]; then
  xattr -dr com.apple.quarantine "$APP/.." 2>/dev/null || true
fi

/usr/bin/python3 "$APP/install.py" "$@"
