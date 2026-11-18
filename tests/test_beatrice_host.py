import struct
import unittest

import numpy as np

from tests.signals import SAMPLE_RATE, load_self_voice, run_blocks, syllable_signal
from voicechanger import BLOCK_SIZE
from voicechanger.beatrice_host import (BeatriceProcessor, build_raw_state_with_model, compute_pitch_shift,
                                        juce_base64_decode, juce_base64_encode, parse_beatrice_state,
                                        read_model_path_from_raw_state, read_model_voices,
                                        serialize_beatrice_state)
from voicechanger.config import Settings
from voicechanger.latency import estimate_lag_samples

SETTINGS = Settings(settings_path=None)
RUNTIME_READY = SETTINGS.beatrice_vst.exists() and SETTINGS.beatrice_model.is_file()


def fake_raw_state(model: str = "") -> bytes:
    entries = [(1, 2, model), (2, 0, 0), (3, 1, 0.0), (5, 1, 52.0)]
    component = serialize_beatrice_state(entries)
    xml = f'<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>{juce_base64_encode(component)}</IComponent></VST3PluginState>'
    xml_bytes = xml.encode("utf-8")
    return b"VC2!" + struct.pack("<I", len(xml_bytes)) + xml_bytes + b"\x00"


class StateEncodingTest(unittest.TestCase):
    def test_juce_base64_round_trip(self) -> None:
        generator = np.random.default_rng(0)
        for size in (0, 1, 2, 3, 4, 5, 63, 4184):
            with self.subTest(size=size):
                data = generator.integers(0, 256, size, dtype=np.uint8).tobytes()
                encoded = juce_base64_encode(data)
                self.assertTrue(encoded.startswith(f"{size}."))
                self.assertEqual(juce_base64_decode(encoded), data)

    def test_state_entries_round_trip(self) -> None:
        entries = [(1, 2, "C:\\モデル\\a.toml"), (2, 0, 3), (4, 1, -1.125)]
        self.assertEqual(parse_beatrice_state(serialize_beatrice_state(entries)), entries)

    def test_model_path_is_replaced_and_frame_kept(self) -> None:
        original = fake_raw_state()
        replaced = build_raw_state_with_model(original, r"E:\x\model.toml")
        self.assertEqual(read_model_path_from_raw_state(replaced), r"E:\x\model.toml")
        magic, length = struct.unpack_from("<4sI", replaced, 0)
        self.assertEqual(magic, b"VC2!")
        self.assertEqual(len(replaced), 8 + length + 1, "JUCE の形式: 長さは NUL を除いた XML のバイト数")
        self.assertEqual(replaced[-1:], b"\x00")

    def test_pitch_shift_formula(self) -> None:
        # GUI と同じ式: 平均目標ピッチ + フォルマント - 自分の声の高さ（+ 微調整）、1/8 半音に丸め、±24 に収める
        self.assertEqual(compute_pitch_shift(65.625, 0.0, 43.88), 21.75)
        self.assertEqual(compute_pitch_shift(54.0, 0.5, 52.0, pitch_offset=-1.0), 1.5)
        self.assertEqual(compute_pitch_shift(100.0, 0.0, 0.0), 24.0)
        self.assertEqual(compute_pitch_shift(0.0, 0.0, 100.0), -24.0)


@unittest.skipUnless(RUNTIME_READY, "runtime/beatrice に VST とモデルが無い")
class BeatriceRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processor = BeatriceProcessor(SETTINGS.beatrice_vst, SETTINGS.beatrice_model, SAMPLE_RATE, BLOCK_SIZE,
                                          voice_index=2, average_source_pitch=46.0)
        cls.signal = syllable_signal(6.0)

    def test_model_voices(self) -> None:
        voices = read_model_voices(SETTINGS.beatrice_model)
        self.assertEqual([voice.index for voice in voices], [0, 1, 2, 3])
        self.assertIn("刻鳴時雨", [voice.name for voice in voices])

    def test_converts_with_same_length_and_sound(self) -> None:
        self.processor.reset()
        output = run_blocks(self.processor, self.signal, BLOCK_SIZE)
        self.assertEqual(len(output), (len(self.signal) // BLOCK_SIZE) * BLOCK_SIZE)
        self.assertTrue(np.all(np.isfinite(output)))
        self.assertGreater(float(np.max(np.abs(output))), 0.01)

    def test_latency_constant_matches_measurement(self) -> None:
        voice = load_self_voice(20.0)
        if voice is None:
            self.skipTest("自分の声の録音（self_voice_dir）が無い。合成音では Beatrice の遅延を測れない")
        self.processor.reset()
        output = run_blocks(self.processor, voice, BLOCK_SIZE)
        measured = estimate_lag_samples(voice[:len(output)], output, SAMPLE_RATE)
        self.assertLess(abs(measured - self.processor.latency_samples), BLOCK_SIZE,
                        f"実測 {measured} / 定数 {self.processor.latency_samples}")

    def test_invalid_voice_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.processor.set_voice(4)  # 4 は話者数と同じ＝モーフィング。選ばせない

    def test_voice_change_recomputes_pitch_shift(self) -> None:
        self.processor.set_voice(0, average_source_pitch=46.0, formant_shift=0.0, pitch_offset=0.0)
        self.assertEqual(self.processor.pitch_shift, compute_pitch_shift(65.625, 0.0, 46.0))
        self.processor.set_voice(3)
        self.assertEqual(self.processor.pitch_shift, compute_pitch_shift(50.875, 0.0, 46.0))
        self.processor.set_voice(2)


if __name__ == "__main__":
    unittest.main()
