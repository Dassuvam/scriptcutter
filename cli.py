#!/usr/bin/env python3
"""
ScriptCutter on the command line.

  python3 cli.py analyze --video raw.mp4                  print the decisions
  python3 cli.py analyze --video raw.mp4 --xml cut.xml    also write a Premiere Pro XML
  python3 cli.py analyze --video raw.mp4 --srt cut.srt --view hinglish
                                                          subtitles of the final cut in Hinglish
  python3 cli.py resolve                                  cut the open Resolve timeline (video track 1)
  python3 cli.py resolve --llm gemini --api-key KEY       with an AI second opinion
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cutter import exporters, resolve_bridge  # noqa: E402
from cutter.llm import PROVIDERS  # noqa: E402
from cutter.project import Project  # noqa: E402

TAGS = {"keep": "KEEP  ", "review": "REVIEW", "remove": "CUT   "}


def clock(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def parse_args():
    ap = argparse.ArgumentParser(description="Retake-aware auto cutting for DaVinci Resolve")
    ap.add_argument("command", choices=["analyze", "resolve"])
    ap.add_argument("--video", help="raw recording (analyze)")
    ap.add_argument("--track", type=int, default=1, help="Resolve video track to read (resolve)")
    ap.add_argument("--language", default="auto", help="auto, en, hi, ... (Whisper language code)")
    ap.add_argument("--text", default="auto", choices=["auto", "original", "english"])
    ap.add_argument("--model", default="auto", help="auto or a model file name, e.g. ggml-large-v3.bin")
    ap.add_argument("--sensitivity", type=float, default=0.0)
    ap.add_argument("--policy", default="balanced", choices=["cautious", "balanced", "aggressive"])
    ap.add_argument("--footage", default="talking_head", choices=["talking_head", "screen"])
    ap.add_argument("--max-pause", type=float, default=0.45)
    ap.add_argument("--head-pad", type=float, default=0.10)
    ap.add_argument("--tail-pad", type=float, default=0.16)
    ap.add_argument("--topic", default="")
    ap.add_argument("--script", help="text file with the script you recorded from (Hindi, Hinglish or English)")
    ap.add_argument("--llm", choices=sorted(PROVIDERS))
    ap.add_argument("--llm-model")
    ap.add_argument("--llm-url")
    ap.add_argument("--api-key")
    ap.add_argument("--checkerboard", action=argparse.BooleanOptionalAction, default=True,
                    help="alternate clips on V1/V2 with a small overlap (on by default)")
    ap.add_argument("--overlap-frames", type=int, default=3)
    ap.add_argument("--voice-isolation", action=argparse.BooleanOptionalAction, default=True,
                    help="AI Voice Isolation on the new timeline's audio tracks (Resolve Studio; on by default)")
    ap.add_argument("--markers", action=argparse.BooleanOptionalAction, default=True, help="markers on brown clips")
    ap.add_argument("--xml", help="write a Premiere Pro XML (FCP7 XML) here")
    ap.add_argument("--fcpxml", help="write a Final Cut Pro FCPXML here")
    ap.add_argument("--edl", help="write an EDL here")
    ap.add_argument("--srt", help="write subtitles of the final cut here")
    ap.add_argument("--transcript", help="write the final cut's transcript (.txt) here")
    ap.add_argument("--report", help="write the edit document (.json) here")
    ap.add_argument("--view", default="original", choices=["original", "hinglish", "english"],
                    help="transcript text: spoken language, Hinglish, or English translation")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    return ap.parse_args()


def main():
    args = parse_args()
    settings = {"language": args.language, "text_mode": args.text, "model": args.model,
                "sensitivity": args.sensitivity, "policy": args.policy, "footage": args.footage,
                "max_pause": args.max_pause, "head_pad": args.head_pad, "tail_pad": args.tail_pad, "topic": args.topic}
    if args.script:
        with open(args.script, encoding="utf-8") as f:
            settings["script"] = f.read()
    if args.llm:
        settings.update(llm_provider=args.llm, llm_model=args.llm_model or "", llm_base_url=args.llm_url or "",
                        llm_api_key=args.api_key or os.environ.get(f"{args.llm.upper()}_API_KEY", ""))

    if args.command == "analyze" and not args.video:
        sys.exit("--video is required for analyze")
    project = Project("file" if args.command == "analyze" else "resolve", args.video or "", args.track, settings,
                      use_cache=not args.no_cache)
    outputs = {"premiere": args.xml, "fcp": args.fcpxml, "edl": args.edl, "srt": args.srt, "txt": args.transcript,
               "json": args.report}
    needs_words = any(outputs[k] for k in ("srt", "txt", "json")) or args.view != "original"
    project.run(only=("cut", "words") if needs_words else ("cut",))
    if project.state == "error":
        sys.exit(project.error)
    result = project.result

    if not args.quiet:
        for a in project.analyses.values():
            print(f"\n== {a['name']} ({a['language']}{', via English' if a['translated'] else ''}, {a['model']})")
            for d in a["decisions"]:
                if d["kind"] == "phrase" or d["action"] != "keep":
                    reason = f"  <- {d['reason'][:80]}" if d["reason"] else ""
                    print(f"{TAGS[d['action']]} {d['confidence']:.2f} {clock(d['start'])} {d.get('text', '')[:80]}{reason}")
    st = result["stats"]
    print(f"\n{st['source_seconds'] / 60:.1f} min -> {st['kept_seconds'] / 60:.1f} min in {st['clips']} clips, "
          f"{st['removed_takes']} takes removed, {st['review_clips']} brown clips to review")

    edit = project.edit()
    for fmt, path in outputs.items():
        if path:
            exporters.FORMATS[fmt]["write"](edit, path, args.view)
            print(f"{exporters.FORMATS[fmt]['label']} written: {path}")
    if args.command == "resolve":
        res = resolve_bridge.build(result["plan"], edit["name"], checkerboard=args.checkerboard,
                                   overlap_frames=args.overlap_frames, voice_isolation=args.voice_isolation,
                                   markers=args.markers)
        if res.get("pending"):
            print(res["message"])
        elif res.get("ok"):
            print(f"Created timeline '{res['timeline']}': {res['items']} clips, {res['review_markers']} review markers"
                  + (", A/B overlap" if args.checkerboard else "")
                  + {True: ", Voice Isolation on", False: ", Voice Isolation needs Resolve Studio", None: ""}[
                      res.get("voice_isolation")])
        else:
            sys.exit(f"Timeline was not created: {res.get('error')}")


if __name__ == "__main__":
    main()
