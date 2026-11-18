import unittest

import numpy as np

from voicechanger.ringbuffer import AudioRingBuffer


class AudioRingBufferTest(unittest.TestCase):
    def test_write_then_read_keeps_order_across_wrap(self) -> None:
        ring = AudioRingBuffer(10)
        ring.write(np.arange(7, dtype=np.float32))
        first = np.zeros(5, dtype=np.float32)
        self.assertEqual(ring.read_into(first), 5)
        np.testing.assert_array_equal(first, np.arange(5))
        ring.write(np.arange(7, 13, dtype=np.float32))  # 折り返す
        rest = np.zeros(8, dtype=np.float32)
        self.assertEqual(ring.read_into(rest), 8)
        np.testing.assert_array_equal(rest, np.arange(5, 13))
        self.assertEqual(ring.level, 0)

    def test_overflow_drops_oldest(self) -> None:
        ring = AudioRingBuffer(4)
        ring.write(np.arange(3, dtype=np.float32))
        ring.write(np.arange(3, 6, dtype=np.float32))
        self.assertEqual(ring.overflow_samples, 2)
        output = np.zeros(4, dtype=np.float32)
        self.assertEqual(ring.read_into(output), 4)
        np.testing.assert_array_equal(output, [2, 3, 4, 5])

    def test_large_write_keeps_newest(self) -> None:
        ring = AudioRingBuffer(3)
        ring.write(np.arange(5, dtype=np.float32))
        output = np.zeros(3, dtype=np.float32)
        ring.read_into(output)
        np.testing.assert_array_equal(output, [2, 3, 4])

    def test_short_read_leaves_rest_untouched(self) -> None:
        ring = AudioRingBuffer(8)
        ring.write(np.ones(2, dtype=np.float32))
        output = np.full(4, 9.0, dtype=np.float32)
        self.assertEqual(ring.read_into(output), 2)
        np.testing.assert_array_equal(output, [1, 1, 9, 9])

    def test_discard(self) -> None:
        ring = AudioRingBuffer(8)
        ring.write(np.arange(6, dtype=np.float32))
        self.assertEqual(ring.discard(4), 4)
        output = np.zeros(2, dtype=np.float32)
        ring.read_into(output)
        np.testing.assert_array_equal(output, [4, 5])


if __name__ == "__main__":
    unittest.main()
