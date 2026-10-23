#!/usr/bin/env bash
# Builds dist/ScriptCutter-macOS.zip - the file you upload to a GitHub release.
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(cat "$ROOT/VERSION")"
DIST="$ROOT/dist"
STAGE="$DIST/ScriptCutter"

rm -rf "$DIST"
mkdir -p "$STAGE"

rsync -a --exclude "__pycache__" --exclude "*.pyc" --exclude ".DS_Store" \
  "$ROOT/cutter" "$ROOT/web" "$ROOT/resolve" "$ROOT/tests" \
  "$ROOT/cli.py" "$ROOT/launcher.py" "$ROOT/install.sh" "$ROOT/uninstall.sh" \
  "$ROOT/Install ScriptCutter.command" "$ROOT/Open ScriptCutter.command" \
  "$ROOT/README.md" "$ROOT/CHANGELOG.md" "$ROOT/LICENSE" "$ROOT/VERSION" \
  "$STAGE/"
chmod +x "$STAGE"/*.command "$STAGE/install.sh" "$STAGE/uninstall.sh"

(cd "$DIST" && zip -qry "ScriptCutter-macOS.zip" "ScriptCutter")
cp "$DIST/ScriptCutter-macOS.zip" "$DIST/ScriptCutter-$VERSION-macOS.zip"
(cd "$DIST" && shasum -a 256 ScriptCutter-macOS.zip > ScriptCutter-macOS.zip.sha256)
rm -rf "$STAGE"

echo "Built $DIST/ScriptCutter-macOS.zip ($(du -h "$DIST/ScriptCutter-macOS.zip" | cut -f1)), version $VERSION"
