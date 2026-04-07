from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.lib.asr.lite_asr import Segment
from pipelines.lite_pipeline_impl import (
    LiteArtifacts,
    _build_artifacts,
    _finalize_eng_subtitles,
    _mux_and_embed,
    _require_resume_artifact,
    _write_tts_plan,
    _run_or_resume_mt,
    _run_or_resume_tts,
)


def _make_args(**overrides: object) -> argparse.Namespace:
    base = {
        "video": Path("/tmp/input.mp4"),
        "glossary": None,
        "chs_override_srt": None,
        "eng_override_srt": None,
        "mt_model": "dummy-mt",
        "mt_device": "cpu",
        "mt_cache_dir": None,
        "mt_batch_enable": False,
        "mt_batch_size": 8,
        "offline": True,
        "en_polish_model": None,
        "en_polish_device": "cpu",
        "lt_enable": False,
        "replacements": Path("replacements.json"),
        "en_replace_dict": None,
        "tts_backend": "kokoro_onnx",
        "tts_sample_rate": 24000,
        "tts_split_len": 100,
        "tts_speed_max": 1.15,
        "tts_align_mode": "atempo",
        "tts_plan_safety_margin": 0.02,
        "subtitle_max_cps": 20.0,
        "subtitle_max_chars_per_line": 42,
        "subtitle_max_lines": 2,
        "sample_rate": 16000,
        "coqui_model": "coqui",
        "coqui_device": "cpu",
        "coqui_speaker": None,
        "coqui_language": None,
        "kokoro_model": Path("kokoro.onnx"),
        "kokoro_voices": Path("voices.bin"),
        "kokoro_voice": "af_bella",
        "kokoro_language": "en-us",
        "kokoro_speed": 1.0,
        "erase_subtitle_enable": False,
        "erase_subtitle_method": "delogo",
        "erase_subtitle_coord_mode": "ratio",
        "erase_subtitle_x": 0.0,
        "erase_subtitle_y": 0.78,
        "erase_subtitle_w": 1.0,
        "erase_subtitle_h": 0.22,
        "erase_subtitle_blur_radius": 12,
        "sub_place_enable": False,
        "sub_place_coord_mode": "ratio",
        "sub_place_x": 0.0,
        "sub_place_y": 0.78,
        "sub_place_w": 1.0,
        "sub_place_h": 0.22,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _write_srt(path: Path, lines: list[str]) -> None:
    blocks = []
    for idx, line in enumerate(lines, start=1):
        blocks.append(f"{idx}\n00:00:0{idx-1},000 --> 00:00:0{idx},500\n{line}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


class LitePipelineImplTest(unittest.TestCase):
    def test_build_artifacts_uses_stable_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "job"
            artifacts = _build_artifacts(root)
            self.assertEqual(artifacts.audio_json.name, "audio.json")
            self.assertEqual(artifacts.chs_srt.name, "chs.srt")
            self.assertEqual(artifacts.eng_srt.name, "eng.srt")
            self.assertEqual(artifacts.tts_plan_json.name, "tts_plan.json")
            self.assertEqual(artifacts.tts_wav.name, "tts_full.wav")
            self.assertEqual(artifacts.video_dub.name, "output_en.mp4")
            self.assertEqual(artifacts.video_sub.name, "output_en_sub.mp4")

    def test_require_resume_artifact_raises_clear_error(self) -> None:
        missing = Path("/tmp/definitely_missing_resume_artifact.txt")
        with self.assertRaises(SystemExit) as ctx:
            _require_resume_artifact(missing, resume_from="mt")
        self.assertIn("resume_from=mt", str(ctx.exception))
        self.assertIn(str(missing), str(ctx.exception))

    def test_run_or_resume_mt_uses_existing_eng_srt_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            _write_srt(artifacts.eng_srt, ["Hello world", "Second line"])
            segments = [
                Segment(start=0.0, end=1.0, text="一", translation=""),
                Segment(start=1.1, end=2.0, text="二", translation=""),
            ]
            args = _make_args()

            seg_en = _run_or_resume_mt(args, segments=segments, artifacts=artifacts, resume_from="tts")

            self.assertEqual(seg_en[0].translation, "Hello world")
            self.assertEqual(seg_en[1].translation, "Second line")

    def test_run_or_resume_mt_copies_override_srt_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            override = root / "override_eng.srt"
            _write_srt(override, ["Override line"])
            segments = [Segment(start=0.0, end=1.0, text="一", translation="")]
            args = _make_args(eng_override_srt=override)

            seg_en = _run_or_resume_mt(args, segments=segments, artifacts=artifacts, resume_from="tts")

            self.assertEqual(seg_en[0].translation, "Override line")
            self.assertTrue(artifacts.eng_srt.exists())
            self.assertIn("Override line", artifacts.eng_srt.read_text(encoding="utf-8"))

    def test_run_or_resume_mt_applies_conservative_shorten_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            segments = [Segment(start=0.0, end=2.6, text="一", translation="")]
            translated = [
                Segment(
                    start=0.0,
                    end=2.6,
                    text="一",
                    translation="Well, I am there, actually, and then waiting for you.",
                )
            ]
            args = _make_args(subtitle_max_cps=20.0)

            with patch("pipelines.lite_pipeline_impl._build_mt_runtime", return_value=(None, None, None, None, None)), patch(
                "pipelines.lite_pipeline_impl.load_replacements", return_value=[]
            ), patch("pipelines.lite_pipeline_impl.translate_segments", return_value=translated), patch(
                "pipelines.lite_pipeline_impl.load_glossary", return_value=[]
            ):
                seg_en = _run_or_resume_mt(args, segments=segments, artifacts=artifacts, resume_from=None)

            self.assertEqual(seg_en[0].translation, "Well, I'm there, actually, and then waiting for you.")
            self.assertIn("I'm", artifacts.eng_srt.read_text(encoding="utf-8"))

    def test_run_or_resume_tts_reuses_existing_audio_for_mux_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            artifacts.tts_wav.write_bytes(b"wav")
            args = _make_args()
            segs = [Segment(start=0.0, end=1.0, text="一", translation="Hello")]

            with patch("pipelines.lite_pipeline_impl.build_kokoro_tts") as build_kokoro, patch(
                "pipelines.lite_pipeline_impl.synthesize_segments_kokoro"
            ) as synth:
                _run_or_resume_tts(
                    args,
                    seg_en=segs,
                    artifacts=artifacts,
                    resume_from="mux",
                    audio_total_ms=None,
                )
                build_kokoro.assert_not_called()
                synth.assert_not_called()

    def test_run_or_resume_tts_requires_audio_for_mux_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            args = _make_args()
            segs = [Segment(start=0.0, end=1.0, text="一", translation="Hello")]

            with self.assertRaises(SystemExit) as ctx:
                _run_or_resume_tts(
                    args,
                    seg_en=segs,
                    artifacts=artifacts,
                    resume_from="mux",
                    audio_total_ms=None,
                )
            self.assertIn("resume_from=mux", str(ctx.exception))
            self.assertIn("tts_full.wav", str(ctx.exception))

    def test_run_or_resume_tts_generates_silence_when_segments_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            args = _make_args(tts_sample_rate=24000, sample_rate=16000)

            silent_segment = object()
            silent_audio = type("SilentAudio", (), {"set_frame_rate": lambda self, rate: silent_segment})()

            with patch("pipelines.lite_pipeline_impl.AudioSegment") as audio_cls, patch(
                "pipelines.lite_pipeline_impl.save_audio"
            ) as save_audio_fn, patch("pipelines.lite_pipeline_impl.build_kokoro_tts") as build_kokoro:
                audio_cls.silent.return_value = silent_audio
                _run_or_resume_tts(
                    args,
                    seg_en=[],
                    artifacts=artifacts,
                    resume_from=None,
                    audio_total_ms=2350.0,
                )

            audio_cls.silent.assert_called_once_with(duration=2350)
            save_audio_fn.assert_called_once_with(silent_segment, artifacts.tts_wav, sample_rate=24000)
            build_kokoro.assert_not_called()

    def test_finalize_eng_subtitles_wraps_long_lines_for_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eng_srt = root / "eng.srt"
            segs = [
                Segment(
                    start=0.0,
                    end=2.0,
                    text="一",
                    translation="This is a deliberately long English subtitle line for lightweight delivery readability checks",
                )
            ]

            _finalize_eng_subtitles(_make_args(), segs, eng_srt)

            self.assertTrue(eng_srt.exists())
            self.assertIn("\n", segs[0].translation or "")
            self.assertIn("\n", eng_srt.read_text(encoding="utf-8"))

    def test_finalize_eng_subtitles_honors_tighter_char_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            eng_srt = root / "eng.srt"
            segs = [
                Segment(
                    start=0.0,
                    end=2.0,
                    text="一",
                    translation="This subtitle should wrap earlier when the lite gate asks for tighter lines",
                )
            ]

            _finalize_eng_subtitles(_make_args(subtitle_max_chars_per_line=24), segs, eng_srt)

            self.assertTrue(eng_srt.exists())
            self.assertGreaterEqual((segs[0].translation or "").count("\n"), 1)
            self.assertTrue(all(len(line) <= 24 for line in (segs[0].translation or "").splitlines()))

    def test_write_tts_plan_forwards_delivery_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            segs = [Segment(start=0.0, end=1.0, text="一", translation="Hello world")]
            args = _make_args(min_sub_dur=1.5, mux_slow_max_ratio=1.18, tts_plan_safety_margin=0.12)
            with patch("pipelines.lite_pipeline_impl.apply_tts_plan") as plan_fn:
                plan_fn.return_value = {"enabled": True, "rebalanced": False, "plans": []}
                _write_tts_plan(args, segs, video_path=Path("/tmp/video.mp4"), tts_plan_json=root / "tts_plan.json")
                kwargs = plan_fn.call_args.kwargs
                self.assertEqual(kwargs["min_dur"], 1.5)
                self.assertEqual(kwargs["tts_plan_safety_margin"], 0.12)
                self.assertNotIn("mux_tail_pad_max_s", kwargs)

    def test_mux_and_embed_disables_tail_pad_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            artifacts.tts_wav.write_bytes(b"wav")
            artifacts.eng_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            args = _make_args(video=Path("/tmp/input.mp4"))
            with patch("pipelines.lite_pipeline_impl.mux_video_audio") as mux_fn, patch("pipelines.lite_pipeline_impl.burn_subtitles") as burn_fn:
                _mux_and_embed(args, artifacts)
                self.assertEqual(mux_fn.call_args.kwargs["tail_pad_max_s"], 0.0)
                burn_fn.assert_called_once()

    def test_mux_and_embed_forwards_erase_and_place_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            artifacts.tts_wav.write_bytes(b"wav")
            artifacts.eng_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            args = _make_args(
                video=Path("/tmp/input.mp4"),
                erase_subtitle_enable=True,
                erase_subtitle_method="fill",
                erase_subtitle_coord_mode="ratio",
                erase_subtitle_x=0.11,
                erase_subtitle_y=0.72,
                erase_subtitle_w=0.77,
                erase_subtitle_h=0.16,
                erase_subtitle_blur_radius=9,
                sub_place_enable=True,
                sub_place_coord_mode="ratio",
                sub_place_x=0.12,
                sub_place_y=0.73,
                sub_place_w=0.78,
                sub_place_h=0.17,
            )

            with patch("pipelines.lite_pipeline_impl.mux_video_audio") as mux_fn, patch(
                "pipelines.lite_pipeline_impl.burn_subtitles"
            ) as burn_fn:
                _mux_and_embed(args, artifacts)

            self.assertEqual(mux_fn.call_args.kwargs["erase_subtitle_method"], "fill")
            self.assertEqual(mux_fn.call_args.kwargs["erase_subtitle_x"], 0.11)
            self.assertEqual(mux_fn.call_args.kwargs["erase_subtitle_h"], 0.16)
            self.assertEqual(mux_fn.call_args.kwargs["erase_subtitle_blur_radius"], 9)
            self.assertEqual(burn_fn.call_args.kwargs["place_x"], 0.12)
            self.assertEqual(burn_fn.call_args.kwargs["place_h"], 0.17)

    def test_mux_and_embed_forwards_subtitle_style_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            artifacts.tts_wav.write_bytes(b"wav")
            artifacts.eng_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
            args = _make_args(
                video=Path("/tmp/input.mp4"),
                sub_font_name="Arial",
                sub_font_size=36,
                sub_outline=2,
                sub_shadow=1,
                sub_margin_v=30,
                sub_alignment=5,
            )

            with patch("pipelines.lite_pipeline_impl.mux_video_audio") as mux_fn, patch(
                "pipelines.lite_pipeline_impl.burn_subtitles"
            ) as burn_fn:
                _mux_and_embed(args, artifacts)

            mux_fn.assert_called_once()
            self.assertEqual(burn_fn.call_args.kwargs["font_name"], "Arial")
            self.assertEqual(burn_fn.call_args.kwargs["font_size"], 36)
            self.assertEqual(burn_fn.call_args.kwargs["outline"], 2)
            self.assertEqual(burn_fn.call_args.kwargs["shadow"], 1)
            self.assertEqual(burn_fn.call_args.kwargs["margin_v"], 30)
            self.assertEqual(burn_fn.call_args.kwargs["alignment"], 5)

    def test_mux_and_embed_copies_video_when_subtitle_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = _build_artifacts(root)
            artifacts.video_dub.write_bytes(b"dub-video")
            artifacts.tts_wav.write_bytes(b"wav")
            artifacts.eng_srt.write_text("", encoding="utf-8")
            args = _make_args(video=Path("/tmp/input.mp4"))

            with patch("pipelines.lite_pipeline_impl.mux_video_audio") as mux_fn, patch(
                "pipelines.lite_pipeline_impl.burn_subtitles"
            ) as burn_fn:
                _mux_and_embed(args, artifacts)

            mux_fn.assert_called_once()
            burn_fn.assert_not_called()
            self.assertEqual(artifacts.video_sub.read_bytes(), b"dub-video")

if __name__ == "__main__":
    unittest.main()
