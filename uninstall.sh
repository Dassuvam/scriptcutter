#!/usr/bin/env bash
# Removes the Resolve menu entry and ScriptCutter's caches. Whisper models are kept unless you agree.
RESOLVE_SCRIPTS="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
rm -f "$RESOLVE_SCRIPTS/ScriptCutterV2.lua" && echo "Removed the Resolve menu entry."
rm -rf "$HOME/Library/Caches/ScriptCutterV2" && echo "Removed the analysis cache."
read -r -p "Also delete what ScriptCutter learned from your choices? [y/N] " A
[[ "$A" =~ ^[Yy]$ ]] && rm -rf "$HOME/Library/Application Support/ScriptCutterV2" && echo "Removed."
read -r -p "Also delete the Whisper models in ~/.cache/whisper-models (several GB)? [y/N] " B
[[ "$B" =~ ^[Yy]$ ]] && rm -rf "$HOME/.cache/whisper-models" && echo "Removed."
echo "Done. You can now delete the ScriptCutter folder."
