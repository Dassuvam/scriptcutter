"""
DaVinci Resolve integration (external scripting API).

v1 built timelines by writing an EDL with source timecodes computed at the
TIMELINE frame rate (24) for 29.97/30 fps sources, so every cut point drifted
by up to ~0.2 s, and it divided timeline frames by the clip fps to get the
clip length, silently dropping the last ~20% of every clip. It also deleted
any existing timeline with the same name.

v2 places clips directly with MediaPool.AppendToTimeline using exact SOURCE
frames, never deletes anything, colours clips (Blue = sure, Brown = review)
and drops a Cocoa marker with the reason on every review clip.
"""
from __future__ import annotations

import os
import sys
import time

RESOLVE_LIBS = [
    "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
    "/Applications/DaVinci Resolve Studio/DaVinci Resolve Studio.app/Contents/Libraries/Fusion/fusionscript.so",
    "/Applications/DaVinci Resolve Studio/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
]
RESOLVE_APIS = [
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
    os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"),
]

COLOR_SURE = "Blue"
COLOR_REVIEW = ["Brown", "Chocolate", "Tan", "Orange"]  # first one Resolve accepts wins
MARKER_REVIEW = "Cocoa"


def _env():
    if "RESOLVE_SCRIPT_LIB" not in os.environ:
        for lib in RESOLVE_LIBS:
            if os.path.exists(lib):
                os.environ["RESOLVE_SCRIPT_LIB"] = lib
                break
    for api in RESOLVE_APIS:
        mod = os.path.join(api, "Modules")
        if os.path.isdir(mod):
            os.environ.setdefault("RESOLVE_SCRIPT_API", api)
            if mod not in sys.path:
                sys.path.append(mod)
            break


def get_resolve():
    _env()
    try:
        import DaVinciResolveScript as dvr  # type: ignore
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def _project(resolve=None):
    resolve = resolve or get_resolve()
    if not resolve:
        raise RuntimeError("DaVinci Resolve is not reachable. Open Resolve and set Preferences > System > "
                           "General > External scripting using = Local.")
    proj = resolve.GetProjectManager().GetCurrentProject()
    if not proj:
        raise RuntimeError("No project is open in DaVinci Resolve.")
    return resolve, proj


def _float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def status() -> dict:
    resolve = get_resolve()
    if not resolve:
        return {"connected": False}
    proj = resolve.GetProjectManager().GetCurrentProject()
    if not proj:
        return {"connected": True, "product": resolve.GetProductName(), "project": None}
    tl = proj.GetCurrentTimeline()
    out = {
        "connected": True,
        "product": resolve.GetProductName(),
        "version": resolve.GetVersionString(),
        "project": proj.GetName(),
        "timeline": tl.GetName() if tl else None,
        "timeline_fps": _float(proj.GetSetting("timelineFrameRate"), 24.0),
        "studio": "Studio" in (resolve.GetProductName() or ""),
    }
    if tl:
        out["video_tracks"] = tl.GetTrackCount("video")
        out["items"] = [i["name"] for i in timeline_sources(tl, track=1)][:50]
    return out


def timeline_sources(tl=None, track: int = 1) -> list:
    """Every clip on a video track of the timeline, with the source range it uses."""
    if tl is None:
        _, proj = _project()
        tl = proj.GetCurrentTimeline()
        if not tl:
            raise RuntimeError("No timeline is open in DaVinci Resolve.")
    out = []
    for it in tl.GetItemListInTrack("video", track) or []:
        mpi = it.GetMediaPoolItem()
        if not mpi:
            continue
        path = mpi.GetClipProperty("File Path")
        if not path or not os.path.isfile(path):
            continue
        fps = _float(mpi.GetClipProperty("FPS"), 0.0)
        src_in = it.GetSourceStartFrame()
        src_out = it.GetSourceEndFrame()
        out.append({
            "name": it.GetName(),
            "path": path,
            "fps": fps,
            "src_in": int(src_in) if src_in is not None else 0,
            "src_out": int(src_out) + 1 if src_out is not None else 0,
            "start_sec": (src_in or 0) / fps if fps else 0.0,
            "end_sec": ((src_out or 0) + 1) / fps if fps else 0.0,
            "media_pool_item": mpi,
            "audio_channels": int(_float(mpi.GetClipProperty("Audio Ch"), 2)),
        })
    return out


def _unique_name(proj, base: str) -> str:
    names = {proj.GetTimelineByIndex(i).GetName() for i in range(1, proj.GetTimelineCount() + 1)}
    if base not in names:
        return base
    k = 2
    while f"{base} {k}" in names:
        k += 1
    return f"{base} {k}"


def _fps_ratio(tl_fps: float, src_fps: float) -> float:
    # Resolve conforms NTSC/integer rates frame-for-frame (29.97 on a 24 timeline uses 24/30)
    return round(tl_fps) / round(src_fps) if src_fps else 1.0


def build_timeline(
    plan: list,
    name: str,
    checkerboard: bool = False,
    overlap_frames: int = 3,
    voice_isolation: bool = False,
    voice_amount: int = 100,
    markers: bool = True,
    log=None,
) -> dict:
    """
    plan: ordered list of {"media_pool_item" | "path", "src_fps", "src_in", "src_out" (exclusive),
                            "status": keep|review, "reasons": [...], "text": str, "ids": [...]}
    """
    log = log or (lambda m: None)
    resolve, proj = _project()
    mp = proj.GetMediaPool()
    tl_fps = _float(proj.GetSetting("timelineFrameRate"), 24.0)

    # media pool items for plain paths
    cache = {}
    for c in plan:
        if c.get("media_pool_item") is None:
            p = c["path"]
            if p not in cache:
                cache[p] = _find_or_import(mp, p)
            c["media_pool_item"] = cache[p]

    final_name = _unique_name(proj, name)
    tl = mp.CreateEmptyTimeline(final_name)
    if not tl:
        raise RuntimeError(f"Resolve could not create timeline '{final_name}'.")
    proj.SetCurrentTimeline(tl)
    tl_start = tl.GetStartFrame()
    # handles fetched before the timeline existed are rejected by AppendToTimeline: fetch them again
    mp = proj.GetMediaPool()
    fresh = {}
    for c in plan:
        p = c.get("path")
        if p and p not in fresh:
            fresh[p] = _find_or_import(mp, p)
        if p:
            c["media_pool_item"] = fresh[p]

    if checkerboard:
        while tl.GetTrackCount("video") < 2:
            tl.AddTrack("video")
        sub = tl.GetTrackSubType("audio", 1) or "stereo"
        while tl.GetTrackCount("audio") < 2:
            tl.AddTrack("audio", sub)

    # record positions (timeline frames)
    rec = 0
    infos = []
    for i, c in enumerate(plan):
        dur = max(1, int(round((c["src_out"] - c["src_in"]) * _fps_ratio(tl_fps, c["src_fps"]))))
        ov = min(overlap_frames, dur // 2) if checkerboard and i > 0 else 0
        start = max(0, rec - ov)
        c["rec_in"], c["rec_out"] = start, start + dur
        c["track"] = (i % 2) + 1 if checkerboard else 1
        rec = start + dur
        info = {
            "mediaPoolItem": c["media_pool_item"],
            "startFrame": int(c["src_in"]),
            "endFrame": int(c["src_out"]) - 1,   # Resolve treats endFrame as inclusive
            "trackIndex": c["track"],
            "recordFrame": tl_start + start,
        }
        infos.append(info)

    t0 = time.time()
    items = []
    # one clip per call: multi-clip AppendToTimeline calls hang Resolve 21.
    # A just-created timeline also rejects appends for a moment, so retry the first one.
    for k, info in enumerate(infos):
        got = mp.AppendToTimeline([info]) or []
        tries = 0
        while not got and k == 0 and tries < 10:
            time.sleep(0.5)
            proj.SetCurrentTimeline(tl)
            got = mp.AppendToTimeline([info]) or []
            tries += 1
        items += list(got)
    log(f"Placed {len(items)} timeline items in {time.time() - t0:.1f}s")
    if not items:
        raise RuntimeError("Resolve refused to place the clips (AppendToTimeline returned nothing).")

    # map placed video items back to plan entries by record start
    by_start = {}
    for tr in range(1, tl.GetTrackCount("video") + 1):
        for it in tl.GetItemListInTrack("video", tr) or []:
            by_start.setdefault((tr, it.GetStart() - tl_start), it)
    a_by_start = {}
    for tr in range(1, tl.GetTrackCount("audio") + 1):
        for it in tl.GetItemListInTrack("audio", tr) or []:
            a_by_start.setdefault((tr, it.GetStart() - tl_start), it)

    review_color = None
    colored, marked, mismatched = 0, 0, 0
    for c in plan:
        key = (c["track"], c["rec_in"])
        v = by_start.get(key)
        if v is None:
            # tolerate 1-frame rounding differences
            v = by_start.get((c["track"], c["rec_in"] + 1)) or by_start.get((c["track"], c["rec_in"] - 1))
        if v is None:
            mismatched += 1
            continue
        # Resolve reports NTSC sources (29.97) in conformed integer-rate frames, so compare in seconds
        fps = c["src_fps"] or 25.0
        if abs(v.GetSourceStartFrame() / round(fps) - c["src_in"] / fps) > 1.5 / fps:
            mismatched += 1
        if c["status"] == "review":
            if review_color is None:
                for col in COLOR_REVIEW:
                    if v.SetClipColor(col):
                        review_color = col
                        break
            else:
                v.SetClipColor(review_color)
        else:
            v.SetClipColor(COLOR_SURE)
        colored += 1
        a = a_by_start.get(key)
        if a is not None:
            a.SetClipColor(review_color if c["status"] == "review" and review_color else COLOR_SURE)
        if markers and c["status"] == "review":
            reason = "; ".join(r for r in c.get("reasons", []) if r) or "Needs a human look"
            note = reason + ("\n\n\"" + c["text"][:400] + "\"" if c.get("text") else "")
            dur = max(1, v.GetDuration())
            if tl.AddMarker(c["rec_in"], MARKER_REVIEW, "REVIEW: " + reason[:60], note, dur,
                            ",".join(c.get("ids", []))[:200]):
                marked += 1

    vi_ok = None
    if voice_isolation:
        vi_ok = True
        for tr in range(1, tl.GetTrackCount("audio") + 1):
            vi_ok = bool(tl.SetVoiceIsolationState(tr, {"isEnabled": True, "amount": int(voice_amount)})) and vi_ok
        if not vi_ok:
            log("Voice Isolation could not be enabled (it needs DaVinci Resolve Studio).")

    return {
        "timeline": final_name,
        "items": len(items),
        "colored": colored,
        "review_markers": marked,
        "review_color": review_color,
        "position_mismatches": mismatched,
        "voice_isolation": vi_ok,
        "timeline_fps": tl_fps,
    }


def _find_or_import(mp, path: str):
    ap = os.path.abspath(path)

    def scan(folder):
        for clip in folder.GetClipList() or []:
            cp = clip.GetClipProperty("File Path")
            if cp and os.path.abspath(cp) == ap:
                return clip
        for sub in folder.GetSubFolderList() or []:
            f = scan(sub)
            if f:
                return f
        return None

    found = scan(mp.GetRootFolder())
    if found:
        return found
    got = mp.ImportMedia([ap])
    if got:
        return got[0]
    raise RuntimeError(f"Resolve could not import {ap}")
