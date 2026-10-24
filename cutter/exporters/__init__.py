"""
Output formats. Each module turns the edit document (cutter/edit.py) into one
file with write(edit, path, view).

To add a format: write the module, add one entry to FORMATS, and (for an
editing app) its key to EDITORS. The page lists EDITORS in the "Export as" menu
through /api/status, so nothing else has to change.

view picks the transcript text: "original" (the spoken language), "hinglish",
or "english" (only when the analysis used Whisper's English translation).
"""
from __future__ import annotations

from . import edl, fcp7, fcpxml, transcript, video

# key: label in the page, file ending added to the cut's name, writer, and what to tell the creator afterwards
FORMATS = {
    "mp4": {"label": "MP4 video", "ext": ".mp4", "write": video.write,
            "how": "Straight cuts with the brown parts kept. Ready to watch or upload."},
    "premiere": {"label": "Premiere Pro (XML)", "ext": ".xml", "write": fcp7.write,
                 "how": "In Premiere Pro choose File → Import and pick this file. The cut opens as a new sequence."},
    "fcp": {"label": "Final Cut Pro (XML)", "ext": ".fcpxml", "write": fcpxml.write,
            "how": "In Final Cut Pro choose File → Import → XML and pick this file. The cut opens as a new project."},
    "edl": {"label": "Other editor (EDL)", "ext": ".edl", "write": edl.write,
            "how": "Import this EDL in your editor. If it asks for the media, point it to your original video."},
    "srt": {"label": "Subtitles (.srt)", "ext": " - subtitles.srt", "write": transcript.write_srt, "how": ""},
    "txt": {"label": "Transcript (.txt)", "ext": " - transcript.txt", "write": transcript.write_txt, "how": ""},
    "json": {"label": "Edit data (.json)", "ext": " - edit.json", "write": transcript.write_json, "how": ""},
}
EDITORS = ("premiere", "fcp", "edl")  # files for editing apps; "mp4" is the finished video itself


def export(edit: dict, fmt: str, base_path: str, view: str = "original") -> str:
    """Write one format to base_path + its file ending; returns the file path."""
    spec = FORMATS[fmt]
    path = base_path + spec["ext"]
    spec["write"](edit, path, view)
    return path
