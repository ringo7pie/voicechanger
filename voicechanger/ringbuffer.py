"""モノラル float32 のリングバッファ。

入力・出力のコールバック（PortAudio のスレッド）と処理ループの間の受け渡しに使う。
書き手 1 つ・読み手 1 つを前提に、短いロックで守る。
"""
from __future__ import annotations

import threading

import numpy as np


class AudioRingBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity は正の数にする")
        self._buffer = np.zeros(capacity, dtype=np.float32)
        self._capacity = capacity
        self._read_index = 0
        self._level = 0
        self._lock = threading.Lock()
        self.overflow_samples = 0  # 溢れて捨てた（古い方を上書きした）サンプル数

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def level(self) -> int:
        with self._lock:
            return self._level

    def clear(self) -> None:
        with self._lock:
            self._read_index = 0
            self._level = 0

    def write(self, samples: np.ndarray) -> None:
        """書き込む。溢れる場合は古いサンプルを捨てて新しい方を残す。"""
        count = len(samples)
        if count == 0:
            return
        with self._lock:
            if count >= self._capacity:
                dropped = self._level + count - self._capacity
                self._buffer[:] = samples[-self._capacity:]
                self._read_index = 0
                self._level = self._capacity
                self.overflow_samples += dropped
                return
            free = self._capacity - self._level
            if count > free:
                dropped = count - free
                self._read_index = (self._read_index + dropped) % self._capacity
                self._level -= dropped
                self.overflow_samples += dropped
            write_index = (self._read_index + self._level) % self._capacity
            first = min(count, self._capacity - write_index)
            self._buffer[write_index:write_index + first] = samples[:first]
            if first < count:
                self._buffer[:count - first] = samples[first:]
            self._level += count

    def read_into(self, destination: np.ndarray) -> int:
        """destination を先頭から埋める。読めた数を返す（足りない分は触らない）。"""
        with self._lock:
            count = min(len(destination), self._level)
            if count == 0:
                return 0
            first = min(count, self._capacity - self._read_index)
            destination[:first] = self._buffer[self._read_index:self._read_index + first]
            if first < count:
                destination[first:count] = self._buffer[:count - first]
            self._read_index = (self._read_index + count) % self._capacity
            self._level -= count
            return count

    def discard(self, count: int) -> int:
        """先頭から最大 count サンプルを捨てる。捨てた数を返す。"""
        with self._lock:
            removed = min(count, self._level)
            self._read_index = (self._read_index + removed) % self._capacity
            self._level -= removed
            return removed
