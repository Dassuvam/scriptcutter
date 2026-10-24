"""
ScriptCutter tests.  Run:  python3 tests/test_scriptcutter.py
The last test builds a synthetic recording with macOS `say` and runs the full
pipeline (skipped automatically if `say` or whisper-cli is missing).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from cutter import analyze as an  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

from cutter import cuts, exporters, llm, media, paths, pipeline, resolve_bridge, restarts, scriptmatch, text, translit  # noqa: E402
from cutter import edit as edit_doc  # noqa: E402
from cutter.exporters.transcript import _srt_time, plain_text  # noqa: E402
from cutter.project import Project  # noqa: E402
from cutter.energy import Envelope  # noqa: E402
from cutter.transcribe import _regular_loops, build_chunks, clean_segments  # noqa: E402


def seg(start, end, txt, **kw):
    d = {"start": start, "end": end, "text": txt, "p": 0.8, "words": [], "flags": []}
    d.update(kw)
    return d


def decide(segments, policy="balanced", translate=False):
    ph = an.build_phrases(segments)
    return an.apply_policy(an.analyze(ph, translate_mode=translate), policy)


def actions(decs):
    return [d["action"] for d in decs if d["kind"] == "phrase"]


class TextTests(unittest.TestCase):
    def test_devanagari_survives_normalisation(self):
        self.assertEqual(text.tokens("तो आपको क्या करना है"), ["तो", "आपको", "क्या", "करना", "है"])

    def test_contractions_and_numbers(self):
        self.assertEqual(text.tokens("It's three tools"), ["it", "is", "3", "tools"])

    def test_stock_share(self):
        self.assertGreaterEqual(text.stock_share("Thank you for watching. Please subscribe to our channel."), 0.9)
        self.assertLess(text.stock_share("Thank you for staying, now open the colour page"), 0.5)

    def test_loop_detection(self):
        self.assertTrue(text.has_loop("ले ले ले ले ले ले ले ले ले ले"))
        self.assertFalse(text.has_loop("so first of all you need three tools for this video"))
        self.assertLess(len(text.collapse_loop("a b a b a b a b a b c").split()), 6)


class RetakeTests(unittest.TestCase):
    def test_keep_last_of_three(self):
        d = decide([seg(0, 1.5, "So let's get started."), seg(3, 4.5, "So let's get started."),
                    seg(7, 8.5, "So let's get started.")])
        self.assertEqual(actions(d), ["remove", "remove", "keep"])

    def test_parallel_sentences_are_not_retakes(self):
        d = decide([seg(0, 2, "Now the second tool is Claude"), seg(3, 5, "Claude will work better than the free version"),
                    seg(6, 8, "Now the third tool is Google Flow")])
        self.assertEqual(actions(d), ["keep", "keep", "keep"])

    def test_function_word_false_start(self):
        d = decide([seg(0, 2, "So what you have to do is, as I am..."),
                    seg(3, 9, "So what you have to do is, as I am telling you step by step, take your own story")])
        self.assertEqual(actions(d)[0], "remove")
        self.assertEqual(actions(d)[1], "keep")

    def test_rerecorded_block(self):
        d = decide([
            seg(0, 3, "The first tool is ChatGPT"),
            seg(3.2, 6, "If you have the Go plan of ChatGPT then it is good"),
            seg(6.2, 8, "Which is available for free"),
            seg(9, 12, "The first tool is ChatGPT"),
            seg(12.2, 15, "If you have the Go plan of ChatGPT then it is good"),
            seg(15.2, 18, "Now the second tool is Claude"),
        ])
        acts = actions(d)
        self.assertEqual(acts[0], "remove")
        self.assertEqual(acts[1], "remove")
        self.assertIn(acts[2], ("review", "remove"))  # trapped inside the failed block
        self.assertEqual(acts[3:], ["keep", "keep", "keep"])

    def test_restart_cue(self):
        d = decide([seg(0, 2, "The second thing is good lighting from a window"), seg(2.5, 3.5, "Let me say that again."),
                    seg(4, 7, "The second thing is soft and even lighting from a window")])
        self.assertEqual(actions(d), ["remove", "remove", "keep"])

    def test_far_repetition_is_not_cut(self):
        # same sentence again after a minute of other content = intentional recap, not a retake
        lines = ["Open the colour page first", "Add a serial node for contrast", "Pull the shadows down gently",
                 "Warm up the highlights a little", "Use a qualifier on skin tones", "Soften the mask edges",
                 "Check the scopes while you work", "Match the second camera angle", "Add a subtle film grain",
                 "Render a short test clip"]
        filler = [seg(10 + i * 6, 15 + i * 6, s) for i, s in enumerate(lines)]
        d = decide([seg(0, 3, "Always back up your project file before grading")] + filler +
                   [seg(80, 83, "Always back up your project file before grading")])
        self.assertNotEqual(actions(d)[0], "remove")

    def test_ai_alone_never_cuts(self):
        ph = an.build_phrases([seg(0, 2, "Hello and welcome"), seg(3, 5, "Today we edit faster")])
        decs = an.analyze(ph)
        llm.merge(decs, {"p0001": {"verdict": "remove", "confidence": 1.0, "reason": "intro"}})
        an.apply_policy(decs, "aggressive")
        self.assertEqual(decs[0]["action"], "review")

    def test_ai_can_rescue_a_flag(self):
        ph = an.build_phrases([seg(0, 1.5, "So let's get started."), seg(3, 4.5, "So let's get started.")])
        decs = an.analyze(ph)
        llm.merge(decs, {"p0001": {"verdict": "keep", "confidence": 0.9, "reason": "intentional"}})
        an.apply_policy(decs)
        self.assertEqual(decs[0]["action"], "review")


def flat_env(duration, speech):
    db = [-70.0] * int(duration * 100)
    for a, b in speech:
        for i in range(int(a * 100), min(len(db), int(b * 100))):
            db[i] = -20.0
    return Envelope(db)


class CutTests(unittest.TestCase):
    def setUp(self):
        self.speech = [(1.0, 3.0), (3.6, 6.0), (6.2, 9.0), (12.0, 14.0)]
        self.env = flat_env(16, self.speech)
        self.decs = [
            {"id": "p1", "start": 1.0, "end": 3.0, "kind": "phrase", "action": "keep", "reason": "", "text": "a", "confidence": 0},
            {"id": "p2", "start": 3.6, "end": 6.0, "kind": "phrase", "action": "remove", "reason": "retake", "text": "b", "confidence": .9},
            {"id": "p3", "start": 6.2, "end": 9.0, "kind": "phrase", "action": "keep", "reason": "", "text": "c", "confidence": 0},
            {"id": "p4", "start": 12.0, "end": 14.0, "kind": "phrase", "action": "review", "reason": "check", "text": "d", "confidence": .5},
        ]

    def test_padding_never_leaks_into_removed_take(self):
        res = cuts.build_clips(self.decs, self.speech, self.env)
        for c in res["clips"]:
            self.assertFalse(c["start"] < 6.0 and c["end"] > 3.6, f"clip {c} overlaps the removed take")

    def test_review_clip_is_separate_and_kept(self):
        res = cuts.build_clips(self.decs, self.speech, self.env)
        statuses = [c["status"] for c in res["clips"]]
        self.assertEqual(statuses.count("review"), 1)
        self.assertEqual(len(res["clips"]), 3)

    def test_frames_do_not_overlap(self):
        res = cuts.build_clips(self.decs, self.speech, self.env)
        fr = cuts.to_frames(res["clips"], 29.97)
        for a, b in zip(fr, fr[1:]):
            self.assertLessEqual(a["src_out"], b["src_in"])


class TranscribeHelpers(unittest.TestCase):
    def test_regular_loop_dropped_but_real_retakes_kept(self):
        loop = [seg(10 + 2 * i, 11.8 + 2 * i, "The same is true for the other side.", chunk=1) for i in range(4)]
        self.assertEqual(_regular_loops(loop), {1, 2, 3})
        real = [seg(252.75, 254, "So let's get started.", chunk=2), seg(255.75, 257, "So let's get started.", chunk=2),
                seg(259.7, 261, "So let's get started.", chunk=2)]
        self.assertEqual(_regular_loops(real), set())

    def test_stock_hallucination_over_typing_is_dropped(self):
        regions = [(0, 60)]
        segs = [seg(10, 24, "Thank you for watching. Please subscribe to our channel."),
                seg(30, 34, "Now open the colour page and add a node")]
        out = clean_segments(segs, regions)
        self.assertEqual([s["text"] for s in out], ["Now open the colour page and add a node"])

    def test_chunks_respect_max_length(self):
        regions = [(i * 3.0, i * 3.0 + 2.5) for i in range(40)]
        ch = build_chunks(regions, 125, None)
        self.assertTrue(all(b - a <= 25.0 for a, b in ch))
        self.assertGreater(len(ch), 3)


VIDEO = "/x/My Video.mov"
PLAN = [{"path": VIDEO, "src_fps": 29.97, "src_in": 30, "src_out": 120, "status": "keep", "reasons": [],
         "text": "hello there"},
        {"path": VIDEO, "src_fps": 29.97, "src_in": 300, "src_out": 360, "status": "review",
         "reasons": ["Maybe a retake"], "text": "again"}]


def fake_analyses(translated=False):
    return {VIDEO: {"path": VIDEO, "name": "My Video.mov", "language": "hi", "translated": translated,
                    "info": {"fps": 30000 / 1001, "duration": 60.0, "has_video": True, "start_timecode": "01:00:00:00",
                             "width": 1920, "height": 1080, "audio_channels": 2, "sample_rate": 48000}}}


def sample_edit(words=None):
    return edit_doc.build("My Cut", PLAN, fake_analyses(translated=words is not None), 0.0, {VIDEO: words})


class EditTests(unittest.TestCase):
    def test_rates(self):
        self.assertEqual(edit_doc.rate(30000 / 1001)["den"], 30000)
        self.assertTrue(edit_doc.rate(23.976)["ntsc"])
        self.assertEqual((edit_doc.rate(25)["timebase"], edit_doc.rate(25)["ntsc"]), (25, False))

    def test_timecode_with_source_start(self):
        self.assertEqual(edit_doc.tc_to_frames("01:00:00:00", 25), 90000)
        self.assertEqual(edit_doc.frames_to_tc(90000 + 37, 25), "01:00:01:12")

    def test_drop_frame_timecode(self):
        # reference values: 29.97 drop-frame skips frame labels ;00 and ;01 each minute except every 10th
        for tc, frames in (("00:01:00;02", 1800), ("00:10:00;00", 17982), ("01:00:00;00", 107892)):
            self.assertEqual(edit_doc.tc_to_frames(tc, 29.97), frames)
            self.assertEqual(edit_doc.frames_to_tc(frames, 29.97, drop_frame=True), tc)

    def test_drop_frame_source_in_exports(self):
        analyses = fake_analyses()
        analyses[VIDEO]["info"]["start_timecode"] = "01:00:00;00"  # a Sony camera at 29.97
        e = edit_doc.build("DF", PLAN, analyses)
        self.assertTrue(e["sources"]["s1"]["drop_frame"])
        tmp = tempfile.mkdtemp()
        with open(exporters.export(e, "edl", os.path.join(tmp, "cut"))) as f:
            self.assertIn("01:00:01;00 01:00:04;00", f.read())  # 107892 + 30 frames, in drop-frame labels
        clip = ET.parse(exporters.export(e, "fcp", os.path.join(tmp, "cut"))).getroot().find(".//spine/asset-clip")
        self.assertEqual(clip.get("tcFormat"), "DF")

    def test_clips_follow_each_other_on_the_timeline(self):
        e = sample_edit()
        self.assertEqual([(c["rec_in"], c["rec_out"]) for c in e["clips"]], [(0, 90), (90, 150)])
        self.assertEqual(e["markers"][0]["frame"], 90)
        self.assertEqual(e["sources"]["s1"]["start_frame"], 108000)

    def test_transcript_has_only_words_in_the_cut(self):
        words = [{"w": "नमस्ते", "start": 1.1, "end": 1.5}, {"w": "हटाया", "start": 5.0, "end": 5.5},
                 {"w": "दोस्तों।", "start": 10.2, "end": 10.6}]
        t = sample_edit(words)["transcript"]
        self.assertEqual([w["text"] for w in t["words"]], ["नमस्ते", "दोस्तों।"])
        self.assertAlmostEqual(t["words"][1]["start"], 90 / 29.97 + 10.2 - 300 / 29.97, places=2)
        self.assertEqual([ln["text"] for ln in t["hinglish"]], ["namaste", "doston."])  # 2.7 s apart: two lines
        self.assertEqual(t["english"][0]["text"], "hello there")


class MediaTests(unittest.TestCase):
    @staticmethod
    def probe(video: dict = None, audio: bool = True, duration: str = "10.0") -> dict:
        streams = ([dict({"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "30/1",
                          "avg_frame_rate": "30/1"}, **(video or {}))] if video is not None else [])
        streams += [{"codec_type": "audio", "channels": 2, "sample_rate": "48000", "duration": "12.5"}] if audio else []
        return media.describe({"streams": streams, "format": {"duration": duration} if duration else {}}, "/x/a.mov")

    def test_phone_video_recorded_upright(self):
        new = self.probe({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]})
        old = self.probe({"tags": {"rotate": "90"}})
        self.assertEqual([(i["width"], i["height"]) for i in (new, old)], [(1080, 1920), (1080, 1920)])

    def test_nonsense_frame_rate_falls_back_to_the_average(self):
        self.assertAlmostEqual(self.probe({"r_frame_rate": "90000/1", "avg_frame_rate": "30000/1001"})["fps"], 29.97, 2)

    def test_missing_duration_uses_the_longest_stream(self):
        self.assertEqual(self.probe({}, duration="")["duration"], 12.5)

    def test_cover_art_and_no_sound(self):
        art = self.probe({"disposition": {"attached_pic": 1}})
        silent = self.probe({}, audio=False)
        self.assertFalse(art["has_video"])
        self.assertEqual((silent["audio_streams"], silent["audio_channels"]), (0, 0))


class PlanTests(unittest.TestCase):
    """Clips nothing can be judged in stay whole instead of vanishing from the cut."""

    def plan(self, analysis):
        return pipeline.plan_for_sources([{"path": "/x.mov", "fps": 25.0, "start_sec": 2.0, "end_sec": 6.0}],
                                         {"/x.mov": analysis}, {})

    def test_clip_without_sound_is_kept_whole(self):
        info = {"path": "/x.mov", "duration": 10.0, "fps": 25.0, "has_video": True, "audio_streams": 0}
        res = self.plan(pipeline.silent_analysis(info))
        self.assertEqual([(c["src_in"], c["src_out"], c["status"]) for c in res["plan"]], [(50, 150, "keep")])

    def test_clip_without_speech_is_kept_whole(self):
        analysis = {"info": {"duration": 10.0, "fps": 25.0}, "_env": flat_env(10, []), "phrases": [], "decisions": [],
                    "regions": []}
        self.assertEqual(len(self.plan(analysis)["plan"]), 1)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.edit = sample_edit()

    def write(self, fmt):
        return exporters.export(self.edit, fmt, os.path.join(self.dir, "cut"))

    def test_edl_uses_source_timecode(self):
        with open(self.write("edl")) as f:
            body = f.read()
        self.assertIn("01:00:01:00 01:00:04:00 01:00:00:00 01:00:03:00", body)
        self.assertIn("* REVIEW: Maybe a retake", body)

    def test_premiere_xml(self):
        seq = ET.parse(self.write("premiere")).getroot().find("sequence")
        self.assertEqual(seq.findtext("duration"), "150")
        self.assertEqual(seq.findtext("rate/ntsc"), "TRUE")
        vclips = seq.findall("media/video/track/clipitem")
        self.assertEqual([c.findtext("in") for c in vclips], ["30", "300"])
        self.assertEqual([c.findtext("start") for c in vclips], ["0", "90"])
        self.assertEqual(vclips[0].findtext("file/pathurl"), "file://localhost/x/My%20Video.mov")
        self.assertIsNone(vclips[1].find("file/pathurl"))  # later clips only reference the file
        self.assertEqual(len(seq.findall("media/audio/track")), 2)
        self.assertEqual(len(vclips[0].findall("link")), 3)
        self.assertEqual(vclips[1].findtext("labels/label2"), "Mango")
        self.assertEqual(seq.findtext("marker/in"), "90")

    def test_final_cut_xml(self):
        root = ET.parse(self.write("fcp")).getroot()
        self.assertEqual(root.find("resources/asset").get("start"), "18018/5s")  # 01:00:00:00 at 29.97
        clips = root.findall(".//spine/asset-clip")
        self.assertEqual([c.get("offset") for c in clips], ["0s", "3003/1000s"])
        self.assertEqual(root.find("resources/asset/media-rep").get("src"), "file:///x/My%20Video.mov")
        self.assertIsNotNone(clips[1].find("marker"))

    def test_subtitles(self):
        self.assertEqual(_srt_time(3661.5), "01:01:01,500")
        with open(self.write("srt"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("1\n00:00:00,000 --> "))
        self.assertIn("hello there", plain_text(self.edit))


@unittest.skipUnless(paths.find_program("ffmpeg"), "needs ffmpeg")
class VideoExportTests(unittest.TestCase):
    def test_mp4_has_the_cut_length_in_sync(self):
        import subprocess
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "raw.mp4")
        ffmpeg = paths.find_program("ffmpeg")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=48000", "-t", "4", "-c:v", "libx264" if "libx264" in
                        " ".join(exporters.video.video_encoder(ffmpeg)) else "mpeg4", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", src], check=True)
        analyses = {src: {"path": src, "name": "raw.mp4", "language": "en", "translated": False,
                          "info": {"fps": 25.0, "duration": 4.0, "has_video": True, "start_timecode": "00:00:00:00",
                                   "width": 320, "height": 240, "audio_channels": 1, "sample_rate": 48000}}}
        plan = [{"path": src, "src_fps": 25.0, "src_in": 0, "src_out": 25, "status": "keep", "reasons": [], "text": ""},
                {"path": src, "src_fps": 25.0, "src_in": 50, "src_out": 85, "status": "review", "reasons": [], "text": ""}]
        out = exporters.video.write(edit_doc.build("cut", plan, analyses), os.path.join(tmp, "cut.mp4"))
        probe = subprocess.run([paths.find_program("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,duration",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout.split()
        lengths = {kind: float(sec) for kind, sec in (line.split(",") for line in probe)}
        self.assertAlmostEqual(lengths["video"], 2.4, delta=0.05)  # 25 + 35 frames
        self.assertAlmostEqual(lengths["audio"], lengths["video"], delta=0.03)

    def test_phone_video_recorded_upright_stays_upright(self):
        import subprocess
        ffmpeg, tmp = paths.find_program("ffmpeg"), tempfile.mkdtemp()
        plain, rotated = os.path.join(tmp, "plain.mp4"), os.path.join(tmp, "rotated.mp4")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi",
                        "-i", "sine=sample_rate=48000", "-t", "2", "-pix_fmt", "yuv420p", "-shortest", plain], check=True)
        if subprocess.run([ffmpeg, "-v", "error", "-y", "-display_rotation", "90", "-i", plain, "-c", "copy",
                           rotated]).returncode != 0:
            self.skipTest("this ffmpeg can't write rotation metadata")
        info = media.probe(rotated)
        self.assertEqual((info["width"], info["height"]), (240, 320))
        analyses = {rotated: {"path": rotated, "name": "rotated.mp4", "language": "en", "translated": False, "info": info}}
        plan = [{"path": rotated, "src_fps": 25.0, "src_in": 0, "src_out": 50, "status": "keep", "reasons": []}]
        out = exporters.video.write(edit_doc.build("up", plan, analyses), os.path.join(tmp, "up.mp4"))
        self.assertEqual((media.probe(out)["width"], media.probe(out)["height"]), (240, 320))

    def test_nothing_to_export(self):
        with self.assertRaises(RuntimeError):
            exporters.video.write(edit_doc.build("empty", [], fake_analyses()), os.path.join(tempfile.mkdtemp(), "x.mp4"))


class TranslitTests(unittest.TestCase):
    def test_hinglish(self):
        self.assertEqual(translit.to_hinglish("क्या हाल है दोस्त"), "kya haal hai dost")
        self.assertEqual(translit.to_hinglish("मित्र करना बातें"), "mitra karna baatein")
        self.assertEqual(translit.to_hinglish("चलो, let's go।"), "chalo, let's go.")

    def test_detects_script(self):
        self.assertTrue(translit.has_devanagari("ok नमस्ते"))
        self.assertFalse(translit.has_devanagari("namaste"))


def spoken(text, t0, step=0.3):
    """Timed words, one every `step` seconds."""
    return [{"w": w, "start": round(t0 + i * step, 2), "end": round(t0 + i * step + 0.25, 2)}
            for i, w in enumerate(text.split())]


class RestartTests(unittest.TestCase):
    def test_dropped_attempt_and_scrap_before_it(self):
        words = spoken("and मैं", 7) + spoken("and मेरे को जो सबसे बड़ी", 9) + \
            spoken("और मेरे को जो पूछ सबसे अच्छा लगेगा मैं उसको रीशेयर करूँगा", 12)
        found = sorted(restarts.find(words), key=lambda r: r["start"])
        self.assertEqual([r["start"] for r in found], [7, 9])
        self.assertGreaterEqual(found[1]["confidence"], 0.75)  # cut under Balanced
        self.assertLess(found[1]["end"], 12)

    def test_end_of_a_line_said_again(self):
        words = spoken("तो आप नीचे comments में डाल सकते हैं, and also आपको description में है न,", 0) + \
            spoken("and also आपको description में ये जो video है इसका सारा assets मिल जाएगा", 7)
        (r,) = restarts.find(words)
        self.assertEqual(r["start"], words[8]["start"])  # from "and also", the comments line stays
        self.assertGreaterEqual(r["confidence"], 0.75)

    def test_parallel_sentences_are_not_restarts(self):
        words = spoken("agar aapko ye video pasand aaya to like kar dena.", 0) + \
            spoken("agar aapko ye video pasand nahi aaya to comment karke batana.", 4)
        self.assertEqual(restarts.find(words), [])

    def test_hindi_and_english_spelling_match(self):
        words = spoken("content creation वीडियो editing या AI", 0) + spoken("content creation video editing AI for creators", 4)
        self.assertEqual(len(restarts.find(words)), 1)


class ScriptModeTests(unittest.TestCase):
    SCRIPT = """नमस्ते दोस्तों, आज हम वीडियो बनाना सीखेंगे।
सबसे पहले कैमरा सही जगह पर रखिए।
फिर लाइट ठीक कीजिए।
अंत में सब्सक्राइब ज़रूर करें।"""
    SAID = [(0, "नमस्ते दोस्तों आज हम वीडियो बनाना सीखेंगे"), (5, "सबसे पहले कैमरा"), (8, "let me do that again"),
            (10, "सबसे पहले कैमरा सही जगह पर रखिए"), (14, "सबसे पहले camera को सही जगह पर रखिए"),
            (19, "and by the way this camera is my favourite one from last year"), (24, "phir light theek kijiye")]

    def test_keeps_last_take_of_each_line(self):
        words, phrases = [], []
        for i, (t0, said) in enumerate(self.SAID):
            ws = spoken(said, t0)
            words += ws
            phrases.append({"id": f"p{i + 1:04d}", "start": ws[0]["start"], "end": ws[-1]["end"], "text": said,
                            "p": 0.9, "flags": []})
        a = {"path": "/x.mov", "language": "hi", "translated": False, "info": {"duration": 30.0},
             "phrases": phrases, "decisions": [], "_words": words}
        summary = scriptmatch.apply([{"path": "/x.mov", "start_sec": 0, "end_sec": None}], {"/x.mov": a}, self.SCRIPT)
        self.assertEqual((summary["found"], [m["n"] for m in summary["missing"]]), (3, [4]))
        conf = {d["id"]: d["confidence"] for d in a["decisions"]}
        self.assertEqual(conf["p0001"], 0.0)                    # line 1
        self.assertGreaterEqual(conf["p0002"], 0.75)            # unfinished take of line 2
        self.assertGreaterEqual(conf["p0003"], 0.75)            # "let me do that again"
        self.assertGreaterEqual(conf["p0004"], 0.75)            # earlier take of line 2
        self.assertEqual(conf["p0005"], 0.0)                    # last take of line 2, said a little differently
        self.assertTrue(0.45 <= conf["p0006"] < 0.75)           # unscripted aside: brown
        self.assertEqual(conf["p0007"], 0.0)                    # line 3 said in Hinglish

    def test_whisper_spellings_sound_the_same(self):
        same = [("nekst", "next"), ("stoori", "story"), ("riseyar", "reshare"), ("achha", "accha"), ("karoonga", "karunga")]
        for a, b in same:
            self.assertEqual(scriptmatch.sound_key(a), scriptmatch.sound_key(b), (a, b))
        self.assertNotEqual(scriptmatch.sound_key("video"), scriptmatch.sound_key("camera"))

    def test_later_take_is_never_cut(self):
        words = spoken("chalo shuru karte hain aaj ka video", 0) + spoken("chalo shuru karte hain", 5)
        phrases = [{"id": "p0001", "start": 0, "end": 2.2, "text": "", "p": 0.9, "flags": []},
                   {"id": "p0002", "start": 5, "end": 6.2, "text": "", "p": 0.9, "flags": []}]
        a = {"path": "/x", "language": "hi", "translated": False, "info": {"duration": 9.0}, "phrases": phrases,
             "decisions": [], "_words": words}
        scriptmatch.apply([{"path": "/x", "start_sec": 0, "end_sec": None}], {"/x": a}, "Chalo shuru karte hain aaj ka video.")
        later = next(d for d in a["decisions"] if d["id"] == "p0002")
        self.assertLess(later["confidence"], 0.75)  # weaker, but later: brown at most, never cut

    @staticmethod
    def run_script(script: str, said: list) -> dict:
        """said: [(start, text)] -> confidence per phrase id after script mode."""
        words, phrases = [], []
        for i, (t0, text_) in enumerate(said):
            ws = spoken(text_, t0)
            words += ws
            phrases.append({"id": f"p{i + 1:04d}", "start": ws[0]["start"], "end": ws[-1]["end"], "text": text_,
                            "p": 0.9, "flags": []})
        a = {"path": "/x", "name": "x", "language": "hi", "translated": False, "info": {"duration": said[-1][0] + 30},
             "phrases": phrases, "decisions": [], "_words": words}
        scriptmatch.apply([{"path": "/x", "start_sec": 0, "end_sec": None}], {"/x": a}, script)
        return {d["id"]: d["confidence"] for d in a["decisions"] if d["kind"] == "phrase"}

    def test_a_different_sentence_is_not_a_take(self):
        line = scriptmatch.split_lines("aur agar aapko ye video pasand aaya to like karo.")[0]["toks"]
        other = text.tokens("aur agar aapko kuch bhi doubt hai to comment karo")
        self.assertIsNone(scriptmatch._best_take(line, other, scriptmatch._Similar()))

    def test_a_line_the_script_repeats_is_never_cut(self):
        conf = self.run_script("Chalo dosto aaj naya video banate hain.\nKuch aur baatein.\nChalo dosto aaj naya video banate hain.",
                               [(0, "chalo dosto aaj naya video banate hain"), (60, "chalo dosto aaj naya video banate hain")])
        self.assertEqual(list(conf.values()), [0.0, 0.0])

    def test_a_short_line_cuts_nothing(self):
        conf = self.run_script("Thank you.", [(0, "thank you"), (30, "thank you")])
        self.assertTrue(all(c < 0.75 for c in conf.values()))

    def test_words_in_the_same_breath_as_a_kept_take_are_not_cut(self):
        conf = self.run_script("Description mein aapko saare assets mil jayenge.",
                               [(0, "description mein aapko saare assets mil jayenge to aap wo bhi kar sakte hain")])
        self.assertLess(conf["p0001"], 0.75)

    def test_lines_split_at_sentence_ends(self):
        self.assertEqual(len(scriptmatch.split_lines("Hello there. How are you?\nठीक है। चलो")), 4)


class SplitTests(unittest.TestCase):
    """The review's text cursor: split a line between two words, cut one half, undo."""

    def setUp(self):
        words = [{"w": w, "start": s, "end": s + 0.3} for w, s in
                 (("So", 1.0), ("that's", 1.4), ("it,", 1.8), ("so", 2.6), ("that's", 3.0), ("it", 3.4))]
        self.p = Project("file", "/v.mov")
        self.p.replan = lambda: None  # no audio here: the plan itself is tested in test_split_point_stays_put
        self.p.analyses = {"/v.mov": {"translated": False, "_words": words, "decisions": [
            {"id": "p0001", "start": 1.0, "end": 3.7, "kind": "phrase", "text": "So that's it, so that's it",
             "action": "review", "confidence": 0.6, "reason": "Said again"},
            {"id": "p0001r0", "start": 1.0, "end": 2.1, "kind": "restart", "parent": "p0001", "text": "So that's it,",
             "action": "remove", "confidence": 0.84, "reason": "Started this line"},
            {"id": "p0002", "start": 5.0, "end": 6.0, "kind": "phrase", "text": "later", "ref": "p0001",
             "action": "remove", "confidence": 0.9, "reason": "Said again"}]}}

    def decisions(self):
        return self.p.analyses["/v.mov"]["decisions"]

    def test_split_between_two_words(self):
        second = self.p.split("/v.mov", "p0001", 3)
        first, rest = self.decisions()[0], self.decisions()[1]
        self.assertEqual((first["id"], rest["id"], second), ("p0001-1", "p0001-2", "p0001-2"))
        self.assertEqual((first["text"], rest["text"]), ("So that's it,", "so that's it"))
        self.assertAlmostEqual(first["end"], 2.35)  # the middle of the pause between "it," and "so"
        self.assertEqual(rest["start"], first["end"])
        self.assertEqual(self.decisions()[2]["parent"], "p0001-1")  # the restart inside follows its half
        self.assertEqual(self.decisions()[3]["ref"], "p0001-1")
        self.assertEqual([len(t["words"]) for t in (self.p._texts(self.p.analyses["/v.mov"])[i]
                                                    for i in ("p0001-1", "p0001-2"))], [3, 3])

    def test_undo_takes_back_choices_and_splits(self):
        self.p.split("/v.mov", "p0001", 3)
        self.p.set_decision("/v.mov", "p0001-1", "remove")
        self.p.undo()
        self.assertIsNone(self.decisions()[0].get("user_action"))
        self.p.undo()
        self.assertEqual([d["id"] for d in self.decisions()], ["p0001", "p0001r0", "p0002"])
        with self.assertRaises(RuntimeError):
            self.p.undo()

    def test_cursor_at_the_edge_does_not_split(self):
        for word in (0, 6):
            with self.assertRaises(RuntimeError):
                self.p.split("/v.mov", "p0001", word)

    def test_split_point_stays_put(self):
        speech = [(1.0, 3.7)]
        decs = [{"id": "p1-1", "start": 1.0, "end": 2.35, "kind": "phrase", "action": "remove", "reason": "retake",
                 "text": "So that's it,", "confidence": 0.9, "fixed_end": True},
                {"id": "p1-2", "start": 2.35, "end": 3.7, "kind": "phrase", "action": "keep", "reason": "",
                 "text": "so that's it", "confidence": 0, "fixed_start": True}]
        clips = cuts.build_clips(decs, speech, flat_env(5, speech))["clips"]
        self.assertEqual([c["start"] for c in clips], [2.35])
        decs[0]["action"] = "review"  # a brown half next to a kept half: the edge between them stays too
        clips = cuts.build_clips(decs, speech, flat_env(5, speech))["clips"]
        self.assertEqual([(c["status"], c["end"]) for c in clips][0], ("review", 2.35))


class ProjectTests(unittest.TestCase):
    def test_missing_file_stops_at_the_cut_step(self):
        p = Project("file", "/no/such/video.mov")
        p.run()
        self.assertEqual(p.state, "error")
        self.assertEqual([s["state"] for s in p.public()["steps"]], ["failed", "waiting"])

    def test_cut_step_flags_brown_clips_until_the_cut_is_sent(self):
        p = Project("file", "x.mov")
        p.result, p.step("cut").state = {"stats": {"review_clips": 2}}, "done"
        self.assertEqual(p.public()["steps"][0]["flag"], "2 to check")
        p.approved = True
        self.assertEqual(p.public()["steps"][0]["flag"], "")


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.video = os.path.join(self.dir, "raw.mov")
        open(self.video, "w").close()
        self.p = Project("file", self.video)
        self.p.analyses, self.p.timeline_name = {self.video: {}}, "raw"

    def test_never_overwrites(self):
        first = self.p.output_path("My Cut", ".mp4")
        open(first, "w").close()
        self.assertEqual(os.path.basename(self.p.output_path("My Cut", ".mp4")), "My Cut 2.mp4")

    def test_names_windows_accepts(self):
        self.assertEqual(os.path.basename(self.p.output_path('a:b/c?d. ', ".xml")), "abcd.xml")

    @unittest.skipIf(paths.IS_WINDOWS or os.geteuid() == 0, "folder permissions work differently here")
    def test_read_only_folder_falls_back(self):
        fallback = tempfile.mkdtemp()
        saved, paths.output_dir = paths.output_dir, lambda: fallback
        os.chmod(self.dir, 0o555)
        try:
            self.assertEqual(os.path.dirname(self.p.output_path("cut", ".mp4")), fallback)
        finally:
            os.chmod(self.dir, 0o755)
            paths.output_dir = saved

    def test_nothing_to_export(self):
        self.p.result = {"plan": []}
        with self.assertRaises(RuntimeError):
            self.p.require_clips()


class ServerTests(unittest.TestCase):
    """The local server only answers this computer, and only takes commands from its own page."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        sys.path.insert(0, os.path.join(ROOT, "web"))
        import server
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def ask(self, method: str, path: str, host: str = "", headers: dict = None) -> int:
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=b"{}" if method == "POST" else None,
                     headers=dict({"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"},
                                  **(headers or {})))
        status = conn.getresponse().status
        conn.close()
        return status

    def test_page_answers_this_computer_only(self):
        self.assertEqual(self.ask("GET", "/"), 200)
        self.assertEqual(self.ask("GET", "/", host=f"evil.example:{self.port}"), 403)

    def test_commands_need_the_page_header(self):
        self.assertEqual(self.ask("POST", "/api/job/none/bulk"), 403)
        self.assertEqual(self.ask("POST", "/api/job/none/bulk", headers={"X-ScriptCutter": "1"}), 409)


class LLMParse(unittest.TestCase):
    def test_fenced_json(self):
        items = llm.parse_items('Sure!\n```json\n{"items":[{"id":"p0002","verdict":"remove","confidence":0.8}]}\n```')
        self.assertEqual(items[0]["id"], "p0002")


class LearnTests(unittest.TestCase):
    def test_learns_to_cut_a_category(self):
        from cutter import learn
        learn.PATH = os.path.join(tempfile.mkdtemp(), "fb.json")
        decs = [{"id": f"p{i}", "reason": "Sits inside a section that was re-recorded at 1:00", "confidence": 0.55,
                 "user_action": "remove"} for i in range(5)]
        self.assertEqual(learn.record(decs, lambda d: "review"), 5)
        fresh = [{"id": "x", "reason": "Sits inside a section that was re-recorded at 2:00", "confidence": 0.55}]
        learn.apply(fresh)
        self.assertGreaterEqual(fresh[0]["confidence"], 0.75)  # now auto-cut under Balanced


class BridgeTests(unittest.TestCase):
    def test_lua_literal_escapes_everything(self):
        lua = resolve_bridge.to_lua({"name": 'He said "hi"\\n', "n": 3, "ok": True, "clips": [1.5, None]})
        self.assertIn('["name"]="He said \\"hi\\"\\\\n"', lua)
        self.assertIn('["clips"]={1.5,nil}', lua)

    def test_unknown_program_is_empty(self):
        self.assertEqual(paths.find_program("no-such-program-xyz"), "")


class DownloadTests(unittest.TestCase):
    """The two downloads that tools/build_release.py makes for the website's Mac and Windows buttons."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build_release
        cls.out = tempfile.mkdtemp()
        build_release.build(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def entries(self, name):
        import zipfile
        with zipfile.ZipFile(os.path.join(self.out, name)) as z:
            return {i.filename: (i.external_attr >> 16, z.read(i)) for i in z.infolist()}

    def test_mac_download(self):
        files = self.entries("ScriptCutter-Mac.zip")
        mode, _ = files["ScriptCutter/Install ScriptCutter.command"]
        import stat
        self.assertTrue(stat.S_ISREG(mode) and mode & 0o100)  # macOS's unzip keeps "can run" only for typed entries
        for needed in ("ScriptCutter/Read me.txt", "ScriptCutter/app/install.sh", "ScriptCutter/app/install.py",
                       "ScriptCutter/app/launcher.py", "ScriptCutter/app/assets/ScriptCutter.icns"):
            self.assertIn(needed, files)
        self.assertFalse([f for f in files if f.endswith((".bat", ".ps1", ".ico")) or "/tests/" in f])

    def test_windows_download(self):
        files = self.entries("ScriptCutter-Windows.zip")
        _, bat = files["ScriptCutter/Install ScriptCutter.bat"]
        self.assertIn(b"\r\n", bat)
        self.assertNotIn(b"\n", bat.replace(b"\r\n", b""))  # cmd.exe needs CRLF everywhere
        _, ps1 = files["ScriptCutter/app/install.ps1"]
        self.assertTrue(all(b < 128 for b in ps1))  # Windows PowerShell reads files without a BOM as ANSI
        for needed in ("ScriptCutter/Read me.txt", "ScriptCutter/app/install.py", "ScriptCutter/app/assets/ScriptCutter.ico"):
            self.assertIn(needed, files)
        self.assertFalse([f for f in files if f.endswith((".command", ".sh", ".icns")) or "/tests/" in f])

    def test_installer_ships_everything_the_app_needs(self):
        import install
        for item in install.APP_FILES:
            self.assertTrue(os.path.exists(os.path.join(ROOT, item)), item)
        files = self.entries("ScriptCutter-Mac.zip")
        self.assertIn("ScriptCutter/app/web/static/js/app.js", files)
        self.assertIn("ScriptCutter/app/resolve/ScriptCutter.lua.template", files)


@unittest.skipUnless(shutil.which("say") and (shutil.which("whisper-cli") or os.path.exists("/opt/homebrew/bin/whisper-cli")),
                     "needs macOS say + whisper-cli")
class EndToEnd(unittest.TestCase):
    def test_synthetic_recording(self):
        import make_synthetic
        from cutter import pipeline
        wav = os.path.join(tempfile.mkdtemp(), "synth.wav")
        truth = make_synthetic.build(wav)
        a = pipeline.analyze_file(wav, {"language": "en"}, pipeline.Reporter(), use_cache=False)
        removed = [d for d in a["decisions"] if d["action"] == "remove"]  # whole lines and word-level cuts
        correct = 0
        for t in truth:
            mid = (t["start"] + t["end"]) / 2
            got = "remove" if any(d["start"] <= mid <= d["end"] for d in removed) else "keep"
            correct += got == t["expected"]
        self.assertGreaterEqual(correct, len(truth) - 1, f"{correct}/{len(truth)} correct")


if __name__ == "__main__":
    unittest.main(verbosity=2)
