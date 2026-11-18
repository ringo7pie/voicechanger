"""エンジン: マイク → 変換 → 仮想マイク（CABLE Input）。

スレッドの役割:
- 入出力のコールバック（PortAudio のスレッド）: リングバッファへの出し入れだけ（push_input / pull_output）。
- 処理ループ（run、メインスレッド）: 変換・モード切り替え・命令の実行をすべてここで行う。
  一部の VST3 は裏スレッドから呼ぶと不具合が出る（pedalboard の文書）ため、VST の操作もここに限る。
- 操作 API（別スレッド）: submit() で命令を積み、処理ループが実行するのを待つだけ。

素の声を録画に漏らさないための規則（knowledge/10-silver/areas/audio-routing.md）:
- 変換で例外が出たら、素の声ではなく無音を出し、モードを mute にする。
- through（素の声）とモニターは保存しない。through は safety.through_timeout_s 秒で mute に戻る。
- OBS が録画中なら through とモニターを拒否し、録画が始まったら止める。
"""
from __future__ import annotations

import ctypes
import gc
import logging
import math
import queue
import sys
import threading
import time
from typing import Any, Callable

import numpy as np

from .config import MODES, PERSISTABLE_MODES, Settings
from .ringbuffer import AudioRingBuffer

log = logging.getLogger("voicechanger.engine")


class ModeUnavailableError(RuntimeError):
    """そのモードの処理器が使えない（読み込みに失敗した等）。"""


class RecordingGuardError(RuntimeError):
    """OBS が録画中のため、素の声やモニターに切り替えられない。"""


def db_to_gain(decibels: float) -> float:
    return float(10.0 ** (decibels / 20.0))


def peak_db(block: np.ndarray) -> float:
    peak = float(np.max(np.abs(block))) if len(block) else 0.0
    return 20.0 * math.log10(peak) if peak > 1e-9 else -180.0


class MuteProcessor:
    name = "mute"
    latency_samples = 0

    def __init__(self, block_size: int) -> None:
        self._zeros = np.zeros(block_size, dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        return self._zeros.copy()

    def reset(self) -> None:
        pass


class ThroughProcessor:
    name = "through"
    latency_samples = 0

    def process(self, block: np.ndarray) -> np.ndarray:
        return np.array(block, dtype=np.float32, copy=True)

    def reset(self) -> None:
        pass


class DelayLine:
    """固定の遅延。遅延を変えると、その瞬間に音が飛ぶ（設定を変えたときだけ起きる）。"""

    def __init__(self, delay_samples: int) -> None:
        self._buffer = np.zeros(max(0, int(delay_samples)), dtype=np.float32)

    @property
    def delay_samples(self) -> int:
        return len(self._buffer)

    def set_delay(self, delay_samples: int) -> None:
        delay_samples = max(0, int(delay_samples))
        current = len(self._buffer)
        if delay_samples > current:
            self._buffer = np.concatenate([np.zeros(delay_samples - current, dtype=np.float32), self._buffer])
        elif delay_samples < current:
            self._buffer = self._buffer[current - delay_samples:].copy()

    def reset(self) -> None:
        self._buffer[:] = 0.0

    def process(self, block: np.ndarray) -> np.ndarray:
        if len(self._buffer) == 0:
            return block
        joined = np.concatenate([self._buffer, block])
        self._buffer = joined[len(block):]
        return joined[:len(block)]


class Limiter:
    """ブロック単位のピークリミッタ。NaN / Inf を 0 にし、最後に天井で切る。"""

    def __init__(self, ceiling_db: float, block_ms: float, release_ms: float = 80.0) -> None:
        self.ceiling = db_to_gain(ceiling_db)
        self.gain = 1.0
        self._release = 1.0 - math.exp(-block_ms / release_ms)
        self.reduction_blocks = 0
        self.nonfinite_blocks = 0

    def process(self, block: np.ndarray) -> np.ndarray:
        if not np.all(np.isfinite(block)):
            self.nonfinite_blocks += 1
            block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)
        peak = float(np.max(np.abs(block))) if len(block) else 0.0
        target = 1.0 if peak <= self.ceiling else self.ceiling / peak
        new_gain = target if target < self.gain else self.gain + (target - self.gain) * self._release
        if target < 1.0:
            self.reduction_blocks += 1
        if self.gain == 1.0 and new_gain == 1.0:
            output = block.astype(np.float32, copy=True)
        else:
            output = (block * np.linspace(self.gain, new_gain, len(block), dtype=np.float32)).astype(np.float32)
        self.gain = new_gain
        np.clip(output, -self.ceiling, self.ceiling, out=output)
        return output


class _Command:
    __slots__ = ("name", "arguments", "done", "result", "error")

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = arguments
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


class Engine:
    def __init__(self, settings: Settings, processors: dict[str, Any], *,
                 unavailable: dict[str, str] | None = None,
                 recording_guard: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        audio = settings.audio
        safety = settings.safety
        self.settings = settings
        self.sample_rate = int(audio["sample_rate"])
        self.block_size = int(audio["block_size"])
        self._block_ms = self.block_size * 1000.0 / self.sample_rate
        self._clock = clock
        self._recording_guard = recording_guard
        self._settings_lock = threading.Lock()
        self._settings_dirty = False

        self._processors: dict[str, Any] = {"mute": MuteProcessor(self.block_size), "through": ThroughProcessor()}
        self._processors.update(processors)
        self.unavailable = dict(unavailable or {})
        self.last_errors: dict[str, str] = {}

        self._align = bool(safety.get("align_latency", True))
        self.aligned_latency_samples = self._compute_aligned_latency()
        self._delays = {name: DelayLine(self._delay_for(name)) for name in self._processors}
        self._limiter = Limiter(float(safety["limiter_ceiling_db"]), self._block_ms)
        self._output_gain_db = float(settings.state.get("output_gain_db", 0.0))
        self._output_gain = db_to_gain(self._output_gain_db)
        self._warmup_blocks = max(0, int(math.ceil(float(safety.get("mode_warmup_ms", 100)) / self._block_ms)))
        self._through_timeout = float(safety["through_timeout_s"])
        self._fade_in = np.linspace(0.0, 1.0, self.block_size, dtype=np.float32)
        self._fade_out = 1.0 - self._fade_in

        self.input_ring = AudioRingBuffer(self.sample_rate)
        self.output_ring = AudioRingBuffer(self.sample_rate)
        self.monitor_ring = AudioRingBuffer(self.sample_rate)
        self._input_condition = threading.Condition()
        self._prefill = int(self.sample_rate * float(audio["output_prefill_ms"]) / 1000)
        self._drift_tolerance = int(self.sample_rate * float(audio["drift_tolerance_ms"]) / 1000)
        self._drift_hard_limit = int(self.sample_rate * float(audio["drift_hard_limit_ms"]) / 1000)
        self._quiet_threshold_db = float(audio["quiet_threshold_db"])

        startup = settings.startup_mode
        self._active = startup if startup in self._processors else "mute"
        self._pending: str | None = None
        self._warmup_left = 0
        self._through_started: float | None = None
        self._monitor = False
        self.notice = ""
        if startup != self._active:
            self.notice = f"起動時のモード {startup} は使えないため mute で始めました（{self.unavailable.get(startup, '理由不明')}）"

        self._commands: "queue.Queue[_Command]" = queue.Queue()
        self.counters = {"blocks": 0, "output_underruns": 0, "monitor_underruns": 0, "drift_drops": 0,
                         "drift_inserts": 0, "forced_drops": 0, "processor_errors": 0}
        self._input_peak_db = -180.0
        self._output_peak_db = -180.0
        self._block_seconds_total = 0.0
        self._block_seconds_worst = 0.0
        self._status: dict[str, Any] = {}
        self._status_lock = threading.Lock()
        self._outputs_started = False
        self._prefill_rings()
        self._refresh_status()

    # ------------------------------------------------------------ 遅延をそろえる
    def _processor_max_latency(self, processor: Any) -> int:
        return int(getattr(processor, "max_latency_samples", processor.latency_samples))

    def _compute_aligned_latency(self) -> int:
        if not self._align:
            return 0
        return max(self._processor_max_latency(processor) for processor in self._processors.values())

    def _delay_for(self, name: str) -> int:
        if not self._align:
            return 0
        return max(0, self.aligned_latency_samples - int(self._processors[name].latency_samples))

    # ------------------------------------------------------------ 入出力（コールバックから呼ばれる）
    def push_input(self, samples: np.ndarray) -> None:
        self.input_ring.write(samples)
        with self._input_condition:
            self._input_condition.notify()

    def pull_output(self, frames: int) -> np.ndarray:
        output = np.zeros(frames, dtype=np.float32)
        if self.output_ring.read_into(output) < frames and self._outputs_started:
            self.counters["output_underruns"] += 1
        return output

    def pull_monitor(self, frames: int) -> np.ndarray:
        output = np.zeros(frames, dtype=np.float32)
        if self._monitor and self.monitor_ring.read_into(output) < frames:
            self.counters["monitor_underruns"] += 1
        return output

    def mark_outputs_started(self) -> None:
        self._outputs_started = True

    def _prefill_rings(self) -> None:
        self.output_ring.clear()
        self.output_ring.write(np.zeros(self._prefill, dtype=np.float32))

    # ------------------------------------------------------------ 命令（操作 API から）
    def submit(self, name: str, timeout: float = 5.0, **arguments: Any) -> Any:
        command = _Command(name, arguments)
        self._commands.put(command)
        if not command.done.wait(timeout):
            raise TimeoutError(f"命令 {name} が {timeout} 秒以内に実行されませんでした（処理ループが止まっている可能性）")
        if command.error is not None:
            raise command.error
        self.save_settings_if_dirty()
        return command.result

    def drain_commands(self) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                handler = getattr(self, f"_command_{command.name}", None)
                if handler is None:
                    raise ValueError(f"未知の命令: {command.name}")
                command.result = handler(**command.arguments)
            except BaseException as error:  # noqa: BLE001 - 呼び出し側へ返す
                command.error = error
            finally:
                command.done.set()

    def save_settings_if_dirty(self) -> None:
        with self._settings_lock:
            if self._settings_dirty:
                self.settings.save()
                self._settings_dirty = False

    def _persist(self, changes: dict[str, Any]) -> None:
        with self._settings_lock:
            self.settings.update_state(changes)
            self._settings_dirty = True

    def _is_recording(self) -> bool:
        if self._recording_guard is None:
            return False
        try:
            return bool(self._recording_guard())
        except Exception as error:  # noqa: BLE001
            log.warning("録画中かを確認できませんでした: %s", error)
            return False

    def _command_set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError(f"モードは {MODES} のどれか（受け取ったのは {mode!r}）")
        if mode not in self._processors:
            raise ModeUnavailableError(f"{mode} は使えません: {self.unavailable.get(mode, '処理器がありません')}")
        if mode == "through" and self._is_recording():
            raise RecordingGuardError("OBS が録画中のため、素の声には切り替えられません")
        self.notice = ""
        target_now = self._pending or self._active
        if mode != target_now:
            if mode == self._active:
                self._pending = None  # 切り替え中の取り消し
            else:
                if mode != "mute":
                    self._processors[mode].reset()
                    self._delays[mode].reset()
                self._pending = mode
                self._warmup_left = 0 if mode == "mute" else self._warmup_blocks
        if mode == "through":
            self._through_started = self._clock()
        if mode in PERSISTABLE_MODES:
            self._persist({"mode": mode})
        return self._mode_summary()

    def _command_set_dsp(self, semitones: float | None = None, formant_factor: float | None = None) -> dict[str, Any]:
        processor = self._require("dsp")
        processor.set_params(processor.semitones if semitones is None else float(semitones),
                             processor.formant_factor if formant_factor is None else float(formant_factor))
        self._delays["dsp"].set_delay(self._delay_for("dsp"))
        self._persist({"dsp": {"semitones": processor.semitones, "formant_factor": processor.formant_factor}})
        return processor.describe()

    def _command_set_ai(self, voice_index: int | None = None, average_source_pitch: float | None = None,
                        formant_shift: float | None = None, pitch_offset: float | None = None,
                        output_gain_db: float | None = None) -> dict[str, Any]:
        processor = self._require("ai")
        processor.set_voice(processor.voice_index if voice_index is None else int(voice_index),
                            average_source_pitch, formant_shift, pitch_offset)
        if output_gain_db is not None:
            processor.set_output_gain(float(output_gain_db))
        description = processor.describe()
        self._persist({"ai": {key: description[key] for key in
                              ("voice_index", "average_source_pitch", "formant_shift", "pitch_offset", "output_gain_db")}})
        return description

    def _command_set_output_gain(self, output_gain_db: float) -> float:
        self._output_gain_db = float(np.clip(output_gain_db, -30.0, 20.0))
        self._output_gain = db_to_gain(self._output_gain_db)
        self._persist({"output_gain_db": self._output_gain_db})
        return self._output_gain_db

    def _command_set_monitor(self, enabled: bool) -> bool:
        if enabled and self._is_recording():
            raise RecordingGuardError("OBS が録画中のため、モニターは使えません（モニターの音がゲーム音のトラックに入るため）")
        if enabled and not self._monitor:
            self.monitor_ring.clear()
            self.monitor_ring.write(np.zeros(self._prefill, dtype=np.float32))
        self._monitor = bool(enabled)
        return self._monitor

    def _command_recording_changed(self, recording: bool) -> None:
        if not recording:
            return
        messages = []
        if self._active == "through" or self._pending == "through":
            self._command_set_mode("mute")
            messages.append("素の声を止めて無音にしました")
        if self._monitor:
            self._monitor = False
            messages.append("モニターを切りました")
        if messages:
            self.notice = "OBS の録画が始まったため、" + "・".join(messages)

    def _require(self, name: str) -> Any:
        if name not in self._processors:
            raise ModeUnavailableError(f"{name} は使えません: {self.unavailable.get(name, '処理器がありません')}")
        return self._processors[name]

    # ------------------------------------------------------------ 処理
    def _run_processor(self, name: str, block: np.ndarray) -> np.ndarray | None:
        try:
            output = self._processors[name].process(block)
            return self._delays[name].process(output)
        except Exception as error:  # noqa: BLE001 - 素の声を出さないため、ここで受け止める
            self.counters["processor_errors"] += 1
            self.last_errors[name] = f"{type(error).__name__}: {error}"
            log.exception("処理器 %s で例外", name)
            return None

    def _fail_to_mute(self, name: str) -> None:
        self._active = "mute"
        self._pending = None
        self._through_started = None
        self.notice = f"{name} の変換でエラーが出たため無音にしました: {self.last_errors.get(name, '')}"

    def process_block(self, block: np.ndarray) -> np.ndarray:
        started = time.perf_counter()
        self._input_peak_db = max(peak_db(block), self._input_peak_db - 1.5)
        active_name = self._active
        output = self._run_processor(active_name, block)
        if output is None:
            self._fail_to_mute(active_name)
            output = np.zeros(self.block_size, dtype=np.float32)
        if self._pending is not None:
            pending_name = self._pending
            pending_output = self._run_processor(pending_name, block)
            if pending_output is None:
                # 切り替え先が失敗しただけなので、今鳴っている方はそのまま。切り替えを取り消す
                self._pending = None
                self.notice = f"{pending_name} に切り替えられませんでした: {self.last_errors.get(pending_name, '')}"
            elif self._warmup_left > 0:
                self._warmup_left -= 1
            else:
                output = output * self._fade_out + pending_output * self._fade_in
                self._active = pending_name
                self._pending = None
                if pending_name != "through":
                    self._through_started = None
        output = self._limiter.process(output * self._output_gain)
        self._output_peak_db = max(peak_db(output), self._output_peak_db - 1.5)
        self._check_through_timeout()
        self.counters["blocks"] += 1
        spent = time.perf_counter() - started
        self._block_seconds_total += spent
        self._block_seconds_worst = max(self._block_seconds_worst, spent)
        return output

    def _check_through_timeout(self) -> None:
        if self._through_started is None:
            return
        if self._clock() - self._through_started >= self._through_timeout:
            self._through_started = None
            if self._active == "through" or self._pending == "through":
                self._pending = "mute"
                self._warmup_left = 0
                self.notice = f"素の声が {self._through_timeout:.0f} 秒続いたため、無音に戻しました"

    def emit(self, output: np.ndarray) -> None:
        self.output_ring.write(output)
        if self._monitor:
            self.monitor_ring.write(output)
            if self.monitor_ring.level > self._prefill + self._drift_hard_limit:
                self.monitor_ring.discard(self.monitor_ring.level - self._prefill)
        self._control_drift(peak_db(output) < self._quiet_threshold_db)

    def _control_drift(self, quiet: bool) -> None:
        """マイクと仮想ケーブルのクロックの差で、出力の溜まり具合がずれていくのを戻す。"""
        level = self.output_ring.level
        excess = level - self._prefill
        if excess > self._drift_hard_limit:
            self.output_ring.discard(excess)
            self.counters["forced_drops"] += 1
        elif quiet and excess > self._drift_tolerance:
            self.output_ring.discard(min(excess, self.block_size))
            self.counters["drift_drops"] += 1
        elif quiet and -excess > self._drift_tolerance:
            self.output_ring.write(np.zeros(min(-excess, self.block_size), dtype=np.float32))
            self.counters["drift_inserts"] += 1

    # ------------------------------------------------------------ ループ
    def run(self, stop_event: threading.Event) -> None:
        boost_current_thread()
        gc.collect()
        gc.freeze()
        previous_interval = sys.getswitchinterval()
        sys.setswitchinterval(0.001)
        block = np.zeros(self.block_size, dtype=np.float32)
        last_status = 0.0
        # 出力のストリームは処理ループより先に動き出すので、途切れはここから数え、溜めもここで入れ直す
        self.input_ring.clear()
        self._prefill_rings()
        self.mark_outputs_started()
        try:
            while not stop_event.is_set():
                with self._input_condition:
                    if self.input_ring.level < self.block_size:
                        self._input_condition.wait(timeout=0.05)
                self.drain_commands()
                while self.input_ring.level >= self.block_size:
                    self.input_ring.read_into(block)
                    self.emit(self.process_block(block))
                self._check_through_timeout()
                now = time.monotonic()
                if now - last_status >= 0.05:
                    self._refresh_status()
                    last_status = now
        finally:
            sys.setswitchinterval(previous_interval)
            gc.unfreeze()

    # ------------------------------------------------------------ 状態
    def _mode_summary(self) -> dict[str, Any]:
        return {"mode": self._active, "pending_mode": self._pending}

    def _refresh_status(self) -> None:
        blocks = max(1, self.counters["blocks"])
        through_remaining = None
        if self._through_started is not None:
            through_remaining = max(0.0, self._through_timeout - (self._clock() - self._through_started))
        processors = {}
        for name in ("dsp", "ai"):
            processor = self._processors.get(name)
            if processor is not None and hasattr(processor, "describe"):
                processors[name] = processor.describe()
        status = {
            "mode": self._active,
            "pending_mode": self._pending,
            "available_modes": [mode for mode in MODES if mode in self._processors],
            "unavailable": dict(self.unavailable),
            "last_errors": dict(self.last_errors),
            "notice": self.notice,
            "monitor": self._monitor,
            "through_remaining_s": through_remaining,
            "output_gain_db": self._output_gain_db,
            "aligned_latency_ms": self.aligned_latency_samples * 1000.0 / self.sample_rate,
            "levels": {"input_peak_db": round(self._input_peak_db, 1), "output_peak_db": round(self._output_peak_db, 1),
                       "limiter_gain_db": round(20 * math.log10(max(self._limiter.gain, 1e-9)), 2)},
            "buffers": {"input_ms": self.input_ring.level * 1000.0 / self.sample_rate,
                        "output_ms": self.output_ring.level * 1000.0 / self.sample_rate,
                        "input_overflow_samples": self.input_ring.overflow_samples,
                        **self.counters},
            "processing": {"average_block_ms": self._block_seconds_total * 1000.0 / blocks,
                           "worst_block_ms": self._block_seconds_worst * 1000.0},
            "processors": processors,
            "dsp_presets": self.settings.dsp_presets,
        }
        with self._status_lock:
            self._status = status

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self._status)

    def refresh_status(self) -> None:
        self._refresh_status()


def boost_current_thread() -> None:
    """プロセスの優先度を「通常以上」にし、このスレッドを MMCSS の Pro Audio に登録する（Windows のみ）。"""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        above_normal_priority_class = 0x00008000
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), above_normal_priority_class)
        avrt = ctypes.WinDLL("avrt")
        avrt.AvSetMmThreadCharacteristicsW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p
        task_index = ctypes.c_ulong(0)
        if not avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(task_index)):
            log.warning("MMCSS への登録に失敗しました")
    except OSError as error:
        log.warning("スレッドの優先度を上げられませんでした: %s", error)


def build_processors(settings: Settings, self_pitch_midi: float | None) -> tuple[dict[str, Any], dict[str, str]]:
    """dsp と ai の処理器を作る。作れなかったものは理由を返す（エンジンはそのモードを選べなくなる）。"""
    from .beatrice_host import BeatriceLoadError, BeatriceProcessor
    from .dsp_shifter import DspUnavailableError, PitchFormantShifter

    sample_rate = int(settings.audio["sample_rate"])
    block_size = int(settings.audio["block_size"])
    processors: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    dsp_state = settings.state.get("dsp", {})
    try:
        processors["dsp"] = PitchFormantShifter(sample_rate, block_size, float(dsp_state.get("semitones", 0.0)),
                                                float(dsp_state.get("formant_factor", 1.0)))
    except DspUnavailableError as error:
        unavailable["dsp"] = str(error)
    ai_state = settings.state.get("ai", {})
    average_source_pitch = ai_state.get("average_source_pitch")
    if average_source_pitch is None:
        average_source_pitch = self_pitch_midi if self_pitch_midi is not None else 52.0
    try:
        processors["ai"] = BeatriceProcessor(
            settings.beatrice_vst, settings.beatrice_model, sample_rate, block_size,
            voice_index=int(ai_state.get("voice_index", 0)),
            average_source_pitch=float(average_source_pitch),
            formant_shift=float(ai_state.get("formant_shift", 0.0)),
            pitch_offset=float(ai_state.get("pitch_offset", 0.0)),
            output_gain_db=float(ai_state.get("output_gain_db", 0.0)))
    except (BeatriceLoadError, ValueError, OSError) as error:
        unavailable["ai"] = str(error)
    return processors, unavailable
