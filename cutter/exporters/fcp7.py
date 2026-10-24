"""
Final Cut Pro 7 XML (xmeml v4): what Premiere Pro imports with File > Import.
DaVinci Resolve, Kdenlive and Shotcut read it too.
Limits: one video track and the first two audio channels; brown clips get the
"Mango" label and a sequence marker with the reason.
"""
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET


def _url(path: str) -> str:
    p = path.replace("\\", "/")
    return "file://localhost" + urllib.parse.quote(p if p.startswith("/") else "/" + p, safe="/:")


def _sub(parent, tag: str, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent, r: dict):
    el = _sub(parent, "rate")
    _sub(el, "timebase", r["timebase"])
    _sub(el, "ntsc", "TRUE" if r["ntsc"] else "FALSE")


def _timecode(parent, r: dict, frame: int, string: str):
    tc = _sub(parent, "timecode")
    _rate(tc, r)
    _sub(tc, "string", string)
    _sub(tc, "frame", frame)
    _sub(tc, "displayformat", "NDF")


def _picture(parent, r: dict, width: int, height: int):
    sc = _sub(parent, "samplecharacteristics")
    _rate(sc, r)
    _sub(sc, "width", width)
    _sub(sc, "height", height)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")


def _sound(parent, sample_rate: int):
    sc = _sub(parent, "samplecharacteristics")
    _sub(sc, "depth", 16)
    _sub(sc, "samplerate", sample_rate)


def _file(parent, fid: str, src: dict, written: set):
    if fid in written:  # full description once, references after that
        _sub(parent, "file", id=fid)
        return
    written.add(fid)
    f = _sub(parent, "file", id=fid)
    _sub(f, "name", src["name"])
    _sub(f, "pathurl", _url(src["path"]))
    _rate(f, src["rate"])
    _sub(f, "duration", src["frames"])
    _timecode(f, src["rate"], src["start_frame"], src["start_tc"])
    media = _sub(f, "media")
    if src["has_video"]:
        _picture(_sub(media, "video"), src["rate"], src["width"], src["height"])
    if src["audio_channels"]:
        audio = _sub(media, "audio")
        _sound(audio, src["sample_rate"])
        _sub(audio, "channelcount", src["audio_channels"])


def write(edit: dict, path: str, view: str = "original") -> str:
    r = edit["rate"]
    root = ET.Element("xmeml", version="4")
    seq = _sub(root, "sequence", id="sequence-1")
    _sub(seq, "name", edit["name"])
    _sub(seq, "duration", edit["duration"])
    _rate(seq, r)
    _timecode(seq, r, 0, "00:00:00:00")
    media = _sub(seq, "media")
    video = _sub(media, "video")
    _picture(_sub(video, "format"), r, edit["width"], edit["height"])
    vtrack = _sub(video, "track")
    audio = _sub(media, "audio")
    _sub(audio, "numOutputChannels", 2)
    _sound(_sub(audio, "format"), edit["sample_rate"])
    n_audio = edit["audio_channels"]
    atracks = [_sub(audio, "track", **({"premiereTrackType": "Stereo", "currentExplodedTrackIndex": ch,
                                         "totalExplodedTrackCount": 2} if n_audio == 2 else {}))
               for ch in range(n_audio)]

    written, counts = set(), {}
    files = {sid: f"file-{i + 1}" for i, sid in enumerate(edit["sources"])}
    for i, c in enumerate(edit["clips"], start=1):
        src = edit["sources"][c["source"]]
        items = []  # (mediatype, track index, clip index in track, id, track element)
        if src["has_video"]:
            counts["v"] = counts.get("v", 0) + 1
            items.append(("video", 1, counts["v"], f"clipitem-{i}", vtrack))
        for ch in range(min(n_audio, src["audio_channels"])):
            counts[ch] = counts.get(ch, 0) + 1
            items.append(("audio", ch + 1, counts[ch], f"clipitem-{i}-a{ch + 1}", atracks[ch]))
        for kind, tindex, _, cid, track in items:
            ci = _sub(track, "clipitem", id=cid)
            _sub(ci, "name", src["name"])
            _sub(ci, "enabled", "TRUE")
            _sub(ci, "duration", src["frames"])
            _rate(ci, src["rate"])
            _sub(ci, "start", c["rec_in"])
            _sub(ci, "end", c["rec_out"])
            _sub(ci, "in", c["src_in"])
            _sub(ci, "out", c["src_out"])
            _file(ci, files[c["source"]], src, written)
            if kind == "audio":
                st = _sub(ci, "sourcetrack")
                _sub(st, "mediatype", "audio")
                _sub(st, "trackindex", tindex)
            if len(items) > 1:
                for lkind, ltrack, lindex, lid, _ in items:
                    ln = _sub(ci, "link")
                    _sub(ln, "linkclipref", lid)
                    _sub(ln, "mediatype", lkind)
                    _sub(ln, "trackindex", ltrack)
                    _sub(ln, "clipindex", lindex)
                    if lkind == "audio":
                        _sub(ln, "groupindex", 1)
            if c["status"] == "review":
                _sub(_sub(ci, "labels"), "label2", "Mango")
    for m in edit["markers"]:
        mk = _sub(seq, "marker")
        _sub(mk, "comment", m["note"])
        _sub(mk, "name", m["name"])
        _sub(mk, "in", m["frame"])
        _sub(mk, "out", m["frame"] + m["duration"])

    ET.indent(root, "  ")
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n')
        f.write(ET.tostring(root, encoding="unicode") + "\n")
    return path
