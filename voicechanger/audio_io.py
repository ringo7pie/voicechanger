"""sounddevice による入出力。入力・出力・モニターを別々のストリームで開く（クロックが別でもよい）。

コールバックはエンジンのリングバッファへの出し入れだけを行う。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

from .devices import DeviceNotFoundError, find_device

log = logging.getLogger("voicechanger.audio_io")


class DeviceOpenError(DeviceNotFoundError):
    """デバイスは見つかったが開けない（他のアプリが占有している、形式が合わない等）。"""


def should_open_monitor(output_name: str | None, monitor_name: str | None) -> bool:
    """モニターは出力と別のデバイスのときだけ開く（同じなら二重に鳴る）。"""
    if not monitor_name:
        return False
    return (output_name or "").casefold() != monitor_name.casefold()


class SoundDeviceBackend:
    def __init__(self, engine: Any, audio_settings: dict[str, Any], *, input_wav: Path | None = None,
                 loop_input_wav: bool = False, open_monitor: bool = True) -> None:
        import sounddevice

        self._sounddevice = sounddevice
        self._engine = engine
        self._settings = audio_settings
        self._sample_rate = int(audio_settings["sample_rate"])
        self._host_api = audio_settings["host_api"]
        self._streams: list[Any] = []
        self._input_wav = input_wav
        self._loop_input_wav = loop_input_wav
        self._wav_thread: threading.Thread | None = None
        self._wav_stop = threading.Event()
        self.status_flags = {"input_overflow": 0, "output_underflow": 0, "monitor_underflow": 0}
        self.devices: dict[str, str | None] = {"input": None, "output": None, "monitor": None}
        self._open_monitor = open_monitor

    def _open_stream(self, role: str, factory, **options: Any) -> Any:
        try:
            return factory(samplerate=self._sample_rate, channels=1, dtype="float32", blocksize=0, latency="low",
                           extra_settings=self._sounddevice.WasapiSettings(auto_convert=True), **options)
        except self._sounddevice.PortAudioError as error:
            raise DeviceOpenError(f"{role} デバイスを開けません（{self.devices.get(role)}）: {error}") from error

    def open(self) -> None:
        sounddevice = self._sounddevice
        output_device = find_device(self._settings["output_device"], "output", self._host_api)
        self.devices["output"] = output_device.name
        self._streams.append(self._open_stream("output", sounddevice.OutputStream, device=output_device.index,
                                               callback=self._on_output))
        if self._input_wav is None:
            input_device = find_device(self._settings["input_device"], "input", self._host_api)
            self.devices["input"] = input_device.name
            self._streams.append(self._open_stream("input", sounddevice.InputStream, device=input_device.index,
                                                   callback=self._on_input))
        else:
            self.devices["input"] = f"WAV: {self._input_wav}"
        monitor_name = self._settings.get("monitor_device")
        if self._open_monitor and monitor_name:
            try:
                monitor_device = find_device(monitor_name, "output", self._host_api)
                if should_open_monitor(output_device.name, monitor_device.name):
                    self.devices["monitor"] = monitor_device.name
                    self._streams.append(self._open_stream("monitor", sounddevice.OutputStream,
                                                           device=monitor_device.index, callback=self._on_monitor))
                else:
                    log.info("モニターは出力と同じデバイスなので開きません: %s", monitor_device.name)
            except Exception as error:  # noqa: BLE001 - モニターは無くても動く
                self.devices["monitor"] = None
                log.warning("モニター出力を開けませんでした: %s", error)

    def start(self) -> None:
        for stream in self._streams:
            try:
                stream.start()
            except self._sounddevice.PortAudioError as error:
                raise DeviceOpenError(f"ストリームを開始できません: {error}") from error
        if self._input_wav is not None:
            self._wav_thread = threading.Thread(target=self._feed_wav, name="wav-input", daemon=True)
            self._wav_thread.start()

    def close(self) -> None:
        self._wav_stop.set()
        for stream in self._streams:
            try:
                stream.stop()
                stream.close()
            except Exception as error:  # noqa: BLE001
                log.warning("ストリームを閉じられませんでした: %s", error)
        self._streams.clear()

    def describe(self) -> dict[str, Any]:
        latencies = {}
        for stream in self._streams:
            try:
                latencies[type(stream).__name__ + str(id(stream) % 1000)] = stream.latency
            except Exception:  # noqa: BLE001
                pass
        return {"devices": dict(self.devices), "status_flags": dict(self.status_flags), "stream_latency_s": latencies}

    # コールバック（PortAudio のスレッド）
    def _on_input(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status.input_overflow:
            self.status_flags["input_overflow"] += 1
        self._engine.push_input(indata[:, 0])

    def _on_output(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        if status.output_underflow:
            self.status_flags["output_underflow"] += 1
        outdata[:, 0] = self._engine.pull_output(frames)

    def _on_monitor(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        if status.output_underflow:
            self.status_flags["monitor_underflow"] += 1
        outdata[:, 0] = self._engine.pull_monitor(frames)

    # WAV をマイクの代わりに実時間で流す（試験用）
    def _feed_wav(self) -> None:
        import time

        from pedalboard.io import AudioFile

        with AudioFile(str(self._input_wav)).resampled_to(self._sample_rate) as audio_file:
            audio = audio_file.read(audio_file.frames).mean(axis=0).astype(np.float32)
        chunk = self._sample_rate // 100
        position = 0
        next_time = time.perf_counter()
        while not self._wav_stop.is_set():
            if position >= len(audio):
                if not self._loop_input_wav:
                    self._engine.push_input(np.zeros(chunk, dtype=np.float32))
                    next_time += chunk / self._sample_rate
                    self._sleep_until(next_time)
                    continue
                position = 0
            piece = audio[position:position + chunk]
            if len(piece) < chunk:
                piece = np.concatenate([piece, np.zeros(chunk - len(piece), dtype=np.float32)])
            self._engine.push_input(piece)
            position += chunk
            next_time += chunk / self._sample_rate
            self._sleep_until(next_time)

    @staticmethod
    def _sleep_until(deadline: float) -> None:
        import time

        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.002))
