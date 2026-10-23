#!/usr/bin/env python3
"""
ScriptCutter v2 - command line.

  python3 cli.py analyze --video raw.mp4                 # print the cut plan
  python3 cli.py analyze --video raw.mp4 --edl cut.edl   # + EDL for File > Import > Timeline
  python3 cli.py resolve                                 # cut the active Resolve timeline (V1)
  python3 cli.py resolve --llm gemini --api-key KEY --topic "AI animation tutorial"
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cutter import pipeline, resolve_api  # noqa: E402
from cutter.export import export_edl, export_report  # noqa: E402

COL = {"keep": "\033[94m", "review": "\033[33m", "remove": "\033[90m", "end": "\033[0m"}


def fmt(t):
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def settings_from(args) -> dict:
    s = {
        "language": args.language, "text_mode": args.text, "model": args.model,
        "sensitivity": args.sensitivity, "policy": args.policy, "footage": args.footage,
        "max_pause": args.max_pause, "head_pad": args.head_pad, "tail_pad": args.tail_pad,
        "topic": args.topic, "checkerboard": args.checkerboard, "overlap_frames": args.overlap_frames,
        "voice_isolation": args.voice_isolation,
    }
    if args.llm:
        s.update(llm_provider=args.llm, llm_model=args.llm_model or "",
                 llm_api_key=args.api_key or os.environ.get(f"{args.llm.upper()}_API_KEY", ""),
                 llm_base_url=args.llm_url or "")
    return s


def print_plan(analyses, result, verbose):
    for a in analyses.values():
        print(f"\n== {a['name']}  ({a['language']}{', via English' if a['translated'] else ''}, {a['model']})")
        if verbose:
            for d in a["decisions"]:
                if d["kind"] != "phrase" and d["action"] == "keep":
                    continue
                c = COL[d["action"]]
                tag = {"keep": "KEEP  ", "review": "REVIEW", "remove": "CUT   "}[d["action"]]
                print(f"{c}{tag} {d['confidence']:.2f} {fmt(d['start'])} {d.get('text', '')[:80]}"
                      f"{('  <- ' + d['reason'][:70]) if d['reason'] else ''}{COL['end']}")
        if a.get("llm"):
            print(f"AI reviewer: {a['llm']}")
    st = result["stats"]
    print(f"\n{st['source_seconds'] / 60:.1f} min -> {st['kept_seconds'] / 60:.1f} min in {st['clips']} clips | "
          f"{st['removed_takes']} takes removed | {st['review_clips']} clips to review (brown, "
          f"{st['review_seconds']:.0f}s)")


def main():
    ap = argparse.ArgumentParser(description="ScriptCutter v2 - retake-aware auto cutter for DaVinci Resolve")
    ap.add_argument("command", choices=["analyze", "resolve"])
    ap.add_argument("--video", help="raw video/audio file (analyze)")
    ap.add_argument("--track", type=int, default=1, help="Resolve video track to read (resolve)")
    ap.add_argument("--language", default="auto")
    ap.add_argument("--text", default="auto", choices=["auto", "original", "english"])
    ap.add_argument("--model", default="auto")
    ap.add_argument("--sensitivity", type=float, default=0.0)
    ap.add_argument("--policy", default="balanced", choices=["cautious", "balanced", "aggressive"])
    ap.add_argument("--footage", default="talking_head", choices=["talking_head", "screen"])
    ap.add_argument("--max-pause", type=float, default=0.45)
    ap.add_argument("--head-pad", type=float, default=0.10)
    ap.add_argument("--tail-pad", type=float, default=0.16)
    ap.add_argument("--topic", default="")
    ap.add_argument("--llm", choices=["gemini", "groq", "openrouter", "ollama", "openai_compatible"])
    ap.add_argument("--llm-model")
    ap.add_argument("--llm-url")
    ap.add_argument("--api-key")
    ap.add_argument("--checkerboard", action="store_true")
    ap.add_argument("--overlap-frames", type=int, default=3)
    ap.add_argument("--voice-isolation", action="store_true")
    ap.add_argument("--edl", help="write an EDL here")
    ap.add_argument("--report", help="write a JSON report here")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()
    s = settings_from(args)

    if args.command == "analyze":
        if not args.video:
            ap.error("--video is required for analyze")
        path = os.path.abspath(args.video)
        sources = [{"path": path, "fps": 0.0, "start_sec": 0.0, "end_sec": None}]
    else:
        items = resolve_api.timeline_sources(track=args.track)
        if not items:
            sys.exit("No clips with media found on that track of the current timeline.")
        sources = items

    analyses = {}
    for i, src in enumerate(sources):
        if src["path"] not in analyses:
            rep = pipeline.Reporter()
            rep.file_index, rep.file_count = i, len(sources)
            analyses[src["path"]] = pipeline.analyze_file(src["path"], s, rep, use_cache=not args.no_cache)
    for src in sources:
        if not src.get("fps"):
            src["fps"] = analyses[src["path"]]["info"]["fps"] or 25.0
    result = pipeline.plan_for_sources(sources, analyses, s)
    print_plan(analyses, result, not args.quiet)

    if args.edl:
        tl_fps = 24.0
        st = resolve_api.status() if args.command == "resolve" else {}
        tl_fps = st.get("timeline_fps", tl_fps)
        export_edl(result["plan"], args.edl, "ScriptCutter v2", tl_fps,
                   {p: a["info"]["start_timecode"] for p, a in analyses.items()})
        print(f"EDL written: {args.edl}")
    if args.report:
        export_report(result, analyses, args.report)
        print(f"Report written: {args.report}")
    if args.command == "resolve":
        st = resolve_api.status()
        res = resolve_api.build_timeline(result["plan"], f"{st.get('timeline') or 'Timeline'} - AI Cut v2",
                                         checkerboard=args.checkerboard, overlap_frames=args.overlap_frames,
                                         voice_isolation=args.voice_isolation, log=print)
        print(f"Created timeline '{res['timeline']}': {res['items']} items, {res['review_markers']} review markers")


if __name__ == "__main__":
    main()
