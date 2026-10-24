"""
FCPXML 1.9: what Final Cut Pro imports with File > Import > XML. DaVinci
Resolve reads it too.

Shape of the file:

    <fcpxml><resources>  <format> per frame size / rate, <asset> per source file (with <media-rep src=...>)
            <library><event><project><sequence><spine>
                asset-clip, asset-clip, ...     the cut, one after another

Times are rational seconds on the frame grid ("3003/30000s" = 90 frames at
29.97). An asset's "start" is the source's first timecode, so a clip's "start"
is that plus its in-point. FCPXML has no clip colours: brown clips get a to-do
marker (completed="0") with the reason instead.
"""
from __future__ import annotations

import math
import os
import urllib.parse
import xml.etree.ElementTree as ET

from .fcp7 import _sub


def _t(frames: int, r: dict) -> str:
    """Frames -> reduced rational seconds, e.g. 90 frames at 29.97 -> '3003/1000s'."""
    if not frames:
        return "0s"
    n, d = frames * r["num"], r["den"]
    g = math.gcd(n, d)
    n, d = n // g, d // g
    return f"{n}s" if d == 1 else f"{n}/{d}s"


def _url(path: str) -> str:
    """file:///Users/me/My%20Video.mov (Windows: file:///C:/Users/...)."""
    p = path.replace("\\", "/")
    return "file://" + urllib.parse.quote(p if p.startswith("/") else "/" + p, safe="/:")


def write(edit: dict, path: str, view: str = "original") -> str:
    r = edit["rate"]
    root = ET.Element("fcpxml", version="1.9")
    resources = _sub(root, "resources")
    formats = {}

    def format_id(rr: dict, width: int, height: int) -> str:
        """One <format> per frame rate + size, created the first time it is needed."""
        key = (rr["num"], rr["den"], width, height)
        if key not in formats:
            formats[key] = f"r{len(formats) + 1}"
            _sub(resources, "format", id=formats[key], frameDuration=_t(1, rr), width=width, height=height)
        return formats[key]

    seq_format = format_id(r, edit["width"], edit["height"])
    for sid, src in edit["sources"].items():
        attrs = {"id": sid, "name": os.path.splitext(src["name"])[0], "start": _t(src["start_frame"], src["rate"]),
                 "duration": _t(src["frames"], src["rate"]), "hasVideo": int(src["has_video"]),
                 "hasAudio": int(bool(src["audio_channels"]))}
        if src["has_video"]:
            attrs["format"] = format_id(src["rate"], src["width"], src["height"])
        if src["audio_channels"]:
            attrs.update(audioSources=1, audioChannels=src["audio_channels"], audioRate=src["sample_rate"])
        asset = _sub(resources, "asset", **attrs)
        _sub(asset, "media-rep", kind="original-media", src=_url(src["path"]))

    event = _sub(_sub(root, "library"), "event", name="ScriptCutter")
    project = _sub(event, "project", name=edit["name"])
    sequence = _sub(project, "sequence", format=seq_format, duration=_t(edit["duration"], r), tcStart="0s",
                    tcFormat="NDF", audioLayout="stereo" if edit["audio_channels"] == 2 else "mono",
                    audioRate="44.1k" if edit["sample_rate"] == 44100 else "48k")
    spine = _sub(sequence, "spine")
    for c in edit["clips"]:
        src = edit["sources"][c["source"]]
        start = _t(src["start_frame"] + c["src_in"], src["rate"])  # source timecode of the in-point
        duration = _t(c["rec_out"] - c["rec_in"], r)
        clip = _sub(spine, "asset-clip", ref=c["source"], offset=_t(c["rec_in"], r),
                    name=os.path.splitext(src["name"])[0], start=start, duration=duration,
                    tcFormat="DF" if src["drop_frame"] else "NDF")
        if c["status"] == "review":
            _sub(clip, "marker", start=start, duration=duration,
                 value=("Check: " + "; ".join(c["reasons"]))[:240], completed=0)

    ET.indent(root, "  ")
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n')
        f.write(ET.tostring(root, encoding="unicode") + "\n")
    return path
