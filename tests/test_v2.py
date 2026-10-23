"""
ScriptCutter v2 tests.  Run:  python3 tests/test_v2.py
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
from cutter import cuts, export, llm, text  # noqa: E402
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
        # v1 stripped everything outside a-z, so Hindi takes became empty
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

    def test_checkerboard_layout(self):
        fr = [{"src_in": 0, "src_out": 60}, {"src_in": 100, "src_out": 160}, {"src_in": 200, "src_out": 230}]
        lay = cuts.layout(fr, 30, 24, overlap_frames=3, checkerboard=True)
        self.assertEqual([c["track"] for c in lay], [1, 2, 1])
        self.assertEqual(lay[0]["rec_out"] - lay[1]["rec_in"], 3)


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


class ExportTests(unittest.TestCase):
    def test_timecode_with_source_start(self):
        self.assertEqual(export.tc_to_frames("01:00:00:00", 25), 90000)
        self.assertEqual(export.frames_to_tc(90000 + 37, 25), "01:00:01:12")

    def test_edl_uses_source_rate(self):
        plan = [{"path": "/x/a.mov", "src_fps": 29.97, "src_in": 300, "src_out": 330, "status": "keep", "reasons": []}]
        out = os.path.join(tempfile.mkdtemp(), "t.edl")
        export.export_edl(plan, out, "t", 24.0, {"/x/a.mov": "00:00:10:00"})
        with open(out) as f:
            body = f.read()
        self.assertIn("00:00:20:00 00:00:21:00", body)  # 10 s start TC + frame 300 @ 30 base


class LLMParse(unittest.TestCase):
    def test_fenced_json(self):
        items = llm.parse_items('Sure!\n```json\n{"items":[{"id":"p0002","verdict":"remove","confidence":0.8}]}\n```')
        self.assertEqual(items[0]["id"], "p0002")


@unittest.skipUnless(shutil.which("say") and (shutil.which("whisper-cli") or os.path.exists("/opt/homebrew/bin/whisper-cli")),
                     "needs macOS say + whisper-cli")
class EndToEnd(unittest.TestCase):
    def test_synthetic_recording(self):
        import make_synthetic
        from cutter import pipeline
        wav = os.path.join(tempfile.mkdtemp(), "synth.wav")
        truth = make_synthetic.build(wav)
        a = pipeline.analyze_file(wav, {"language": "en"}, pipeline.Reporter(), use_cache=False)
        phr = [d for d in a["decisions"] if d["kind"] == "phrase"]
        correct = 0
        for t in truth:
            mid = (t["start"] + t["end"]) / 2
            d = next((d for d in phr if d["start"] - 0.3 <= mid <= d["end"] + 0.3), None)
            got = "remove" if d and d["action"] == "remove" else "keep"
            correct += got == t["expected"]
        self.assertGreaterEqual(correct, len(truth) - 1, f"{correct}/{len(truth)} correct")


if __name__ == "__main__":
    unittest.main(verbosity=2)
