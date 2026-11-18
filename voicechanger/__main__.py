"""python -m voicechanger <コマンド>

  run         起動する（マイク → 変換 → CABLE Input）。Ctrl+C か操作画面の API で止まる
  plan        何も開かずに、使う設定・デバイス・処理器の準備状況を出す
  devices     WASAPI のデバイス一覧
  render      WAV をオフラインで変換する（聴き比べ・試験用）
  listen-page 自分の声の録音を各プリセット・各声で変換した聴き比べページを作る
  self-pitch  自分の声の高さ（Beatrice の Average Source Pitch）を録音から測って保存する
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np

from . import config as config_module
from .config import DATA_DIR, SELF_PITCH_PATH, Settings, load_or_measure_self_pitch, save_json_atomic

CONTROL_PATH = DATA_DIR / "control.json"
LOG_DIR = DATA_DIR / "logs"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DEVICE = 3
EXIT_ALREADY_RUNNING = 4


def configure_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(LOG_DIR / "engine.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def emit(line: str) -> None:
    print(line, flush=True)
    logging.getLogger("voicechanger").info(line)


# ---------------------------------------------------------------- run
def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        still_active = 259
        return exit_code.value == still_active
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_running() -> dict | None:
    """data/control.json の PID が生きていれば、その内容を返す。"""
    if not CONTROL_PATH.is_file():
        return None
    try:
        control = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return control if pid_alive(int(control.get("pid", 0))) else None


def watch_parent(parent_pid: int, stop_event: threading.Event, interval: float = 1.0) -> threading.Thread:
    """親プロセス（GUI）が消えたら stop_event を立てる。GUI が強制終了されてもマイクを握ったまま残らないようにする。"""
    log = logging.getLogger("voicechanger")

    def loop() -> None:
        while not stop_event.wait(interval):
            if not pid_alive(parent_pid):
                log.info("親プロセス %s が終了したので止めます", parent_pid)
                stop_event.set()
                return

    thread = threading.Thread(target=loop, name="parent-watch", daemon=True)
    thread.start()
    return thread


def command_run(arguments: argparse.Namespace) -> int:
    configure_logging(arguments.verbose)
    log = logging.getLogger("voicechanger")
    running = is_running()
    if running is not None:
        emit(f"VOICECHANGER_ALREADY_RUNNING url={running.get('url')} pid={running.get('pid')}")
        return EXIT_ALREADY_RUNNING

    from .audio_io import SoundDeviceBackend
    from .control_server import ControlServer
    from .devices import DeviceNotFoundError
    from .engine import Engine, build_processors
    from .obs_client import ObsWatcher

    stop_event = threading.Event()
    if arguments.parent_pid:
        if not pid_alive(arguments.parent_pid):
            emit(f"VOICECHANGER_START_FAILED 親プロセス {arguments.parent_pid} がありません")
            return EXIT_ERROR
        watch_parent(arguments.parent_pid, stop_event)

    backend = None
    server = None
    obs_watcher = None
    started = time.monotonic()
    try:
        settings = Settings(settings_path=arguments.settings_file) if arguments.settings_file else Settings()
        for role, value in (("input_device", arguments.input_device), ("output_device", arguments.output_device),
                            ("monitor_device", arguments.monitor_device)):
            if value:
                settings.audio[role] = value  # この起動だけの上書き（保存しない）
        self_pitch = load_or_measure_self_pitch(settings)
        processors, unavailable = build_processors(settings, self_pitch)
        for name, reason in unavailable.items():
            log.warning("%s は使えません: %s", name, reason)
        obs_watcher = ObsWatcher.from_settings(settings) if not arguments.no_obs else None
        engine = Engine(settings, processors, unavailable=unavailable,
                        recording_guard=obs_watcher.is_recording if obs_watcher else None)
        backend = SoundDeviceBackend(engine, settings.audio, input_wav=arguments.input_wav,
                                     loop_input_wav=arguments.loop_input, open_monitor=not arguments.no_monitor)
        backend.open()

        def extra_status() -> dict:
            return {"backend": backend.describe(), "obs": obs_watcher.describe() if obs_watcher else None}

        server = ControlServer(engine, settings.control["host"], arguments.port or int(settings.control["port"]),
                               extra_status=extra_status, on_shutdown=stop_event.set)
        server.start()
        backend.start()
        # parent_pid: GUI が「自分が起動したエンジンか」を見分けるのに使う。Windows の venv の python.exe は
        # 本物の Python を子プロセスとして起動する中継役なので、GUI が知っている PID と os.getpid() は一致しない
        save_json_atomic(CONTROL_PATH, {"pid": os.getpid(), "parent_pid": arguments.parent_pid, "port": server.port,
                                        "url": server.url, "token": server.token,
                                        "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
        if obs_watcher is not None:
            obs_watcher.start(lambda recording: engine.submit("recording_changed", recording=recording))
    except DeviceNotFoundError as error:  # DeviceOpenError も含む
        emit(f"VOICECHANGER_DEVICE_ERROR {error}")
        _cleanup_failed_start(backend, server, obs_watcher)
        return EXIT_DEVICE
    except Exception as error:  # noqa: BLE001 - 起動の失敗は 1 行で残し、GUI に理由を見せる
        log.exception("起動に失敗しました")
        emit(f"VOICECHANGER_START_FAILED {type(error).__name__}: {error}")
        _cleanup_failed_start(backend, server, obs_watcher)
        return EXIT_ERROR

    devices = backend.describe()["devices"]
    emit(f"VOICECHANGER_READY url={server.url} mode={engine.status()['mode']} input={devices['input']!r} "
         f"output={devices['output']!r} monitor={devices['monitor']!r} startup_s={time.monotonic() - started:.1f}")
    if arguments.open_browser:
        webbrowser.open(server.url)
    exit_code = EXIT_OK
    try:
        engine.run(stop_event)
    except KeyboardInterrupt:
        emit("Ctrl+C を受けたので止めます")
    except Exception:  # noqa: BLE001
        log.exception("処理ループが止まりました")
        exit_code = EXIT_ERROR
    finally:
        stop_event.set()
        if obs_watcher is not None:
            obs_watcher.stop()
        backend.close()
        server.stop()
        engine.save_settings_if_dirty()
        try:
            CONTROL_PATH.unlink()
        except FileNotFoundError:
            pass
        engine.refresh_status()
        buffers = engine.status()["buffers"]
        emit("VOICECHANGER_STOPPED " + " ".join(f"{key}={value}" for key, value in buffers.items()
                                                  if key not in ("input_ms", "output_ms")))
    return exit_code


def _cleanup_failed_start(backend, server, obs_watcher) -> None:
    for action in (lambda: obs_watcher.stop() if obs_watcher else None,
                   lambda: backend.close() if backend else None,
                   lambda: server.stop() if server else None):
        try:
            action()
        except Exception:  # noqa: BLE001
            logging.getLogger("voicechanger").exception("起動失敗の後片付けで例外")


# ---------------------------------------------------------------- plan / devices
def command_plan(arguments: argparse.Namespace) -> int:  # noqa: ARG001
    from .devices import DeviceNotFoundError, find_device, list_devices
    from .dsp_shifter import is_available

    settings = Settings()
    audio = settings.audio
    emit(f"設定: {settings.config_path}（保存値 {settings.settings_path}）")
    emit(f"起動時のモード: {settings.startup_mode}  出力の音量: {settings.state.get('output_gain_db')} dB")
    emit(f"dsp: {settings.state.get('dsp')}  pylibrb: {'あり' if is_available() else '無し'}")
    emit(f"ai : {settings.state.get('ai')}")
    emit(f"Beatrice VST  : {settings.beatrice_vst}（{'あり' if settings.beatrice_vst.exists() else '無し'}）")
    emit(f"Beatrice model: {settings.beatrice_model}（{'あり' if settings.beatrice_model.is_file() else '無し'}）")
    pitch_text = SELF_PITCH_PATH.read_text(encoding="utf-8").strip() if SELF_PITCH_PATH.is_file() else "未測定（起動時に測る）"
    emit(f"自分の声の高さ: {pitch_text}")
    try:
        devices = list_devices(audio["host_api"])
    except Exception as error:  # noqa: BLE001
        emit(f"デバイス一覧を取れません: {error}")
        return EXIT_DEVICE
    status = EXIT_OK
    for role, kind in (("input_device", "input"), ("output_device", "output"), ("monitor_device", "output")):
        try:
            device = find_device(audio[role], kind, devices=devices)
            emit(f"{role}: {audio[role]!r} → [{device.index}] {device.name}")
        except DeviceNotFoundError as error:
            emit(f"{role}: 見つかりません: {error}")
            if role != "monitor_device":
                status = EXIT_DEVICE
    running = is_running()
    emit(f"起動中: {running['url'] if running else 'いいえ'}")
    return status


def command_devices(arguments: argparse.Namespace) -> int:
    from .devices import default_device_names, list_devices

    devices = list_devices(arguments.host_api)
    if arguments.json:
        defaults = default_device_names(arguments.host_api)
        document = {"host_api": arguments.host_api,
                    "inputs": [device.name for device in devices if device.max_input_channels > 0],
                    "outputs": [device.name for device in devices if device.max_output_channels > 0],
                    "default_input": defaults["input"], "default_output": defaults["output"]}
        print(json.dumps(document, ensure_ascii=False), flush=True)
        return EXIT_OK
    for device in devices:
        emit(f"[{device.index:2d}] in={device.max_input_channels} out={device.max_output_channels} "
             f"sr={device.default_samplerate:.0f}  {device.name}")
    return EXIT_OK


# ---------------------------------------------------------------- render
def make_processor(settings: Settings, mode: str, arguments: argparse.Namespace):
    sample_rate = int(settings.audio["sample_rate"])
    block_size = int(settings.audio["block_size"])
    if mode == "dsp":
        from .dsp_shifter import PitchFormantShifter

        return PitchFormantShifter(sample_rate, block_size, arguments.semitones, arguments.formant)
    if mode == "ai":
        from .beatrice_host import BeatriceProcessor

        source_pitch = arguments.source_pitch
        if source_pitch is None:
            source_pitch = load_or_measure_self_pitch(settings) or 52.0
        return BeatriceProcessor(settings.beatrice_vst, settings.beatrice_model, sample_rate, block_size,
                                 voice_index=arguments.voice, average_source_pitch=source_pitch,
                                 formant_shift=arguments.ai_formant, pitch_offset=arguments.pitch_offset,
                                 output_gain_db=arguments.ai_gain)
    if mode == "through":
        from .engine import ThroughProcessor

        return ThroughProcessor()
    raise ValueError(f"render できないモード: {mode}")


def command_render(arguments: argparse.Namespace) -> int:
    from .render import render_file

    settings = Settings()
    processor = make_processor(settings, arguments.mode, arguments)
    result = render_file(processor, Path(arguments.input), Path(arguments.output),
                         int(settings.audio["sample_rate"]), int(settings.audio["block_size"]))
    emit("RENDERED " + json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def command_listen_page(arguments: argparse.Namespace) -> int:
    from .render import LISTEN_DIR, make_listen_page

    settings = Settings()
    if arguments.input:
        input_paths = [Path(value) for value in arguments.input]
    else:
        voice_dir = settings.self_voice_dir
        if voice_dir is None or not voice_dir.is_dir():
            emit("入力の WAV がありません（--input で指定するか、設定の self_voice_dir を直してください）")
            return EXIT_ERROR
        input_paths = sorted(voice_dir.glob("*.wav"))[:arguments.count]
    started = time.monotonic()
    page = make_listen_page(settings, input_paths, Path(arguments.output_dir) if arguments.output_dir else LISTEN_DIR)
    emit(f"LISTEN_PAGE path={page} inputs={len(input_paths)} seconds={time.monotonic() - started:.1f}")
    return EXIT_OK


def command_self_pitch(arguments: argparse.Namespace) -> int:
    settings = Settings()
    if arguments.force and SELF_PITCH_PATH.is_file():
        SELF_PITCH_PATH.unlink()
    midi = load_or_measure_self_pitch(settings)
    if midi is None:
        emit(f"自分の声の録音が見つかりません（self_voice_dir: {settings.self_voice_dir}）")
        return EXIT_ERROR
    emit(f"SELF_PITCH midi={midi:.2f} file={SELF_PITCH_PATH}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m voicechanger", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="起動する")
    run.add_argument("--port", type=int, default=0, help="操作画面のポート（既定は設定の control.port）")
    run.add_argument("--open-browser", action="store_true", help="起動したら操作画面を開く")
    run.add_argument("--input-wav", type=Path, default=None, help="マイクの代わりに WAV を実時間で流す（試験用）")
    run.add_argument("--loop-input", action="store_true", help="--input-wav を繰り返す")
    run.add_argument("--no-monitor", action="store_true", help="モニター出力のストリームを開かない")
    run.add_argument("--input-device", default="", help="入力デバイスの名前（この起動だけ設定を上書き）")
    run.add_argument("--output-device", default="", help="出力デバイスの名前（この起動だけ設定を上書き）")
    run.add_argument("--monitor-device", default="", help="モニターのデバイスの名前（この起動だけ設定を上書き）")
    run.add_argument("--parent-pid", type=int, default=0, help="このプロセスが消えたら止まる（GUI から起動するとき）")
    run.add_argument("--settings-file", type=Path, default=None,
                     help="保存値の読み書きに使うファイル（既定は data/settings.json。試験で本物を書き換えないため）")
    run.add_argument("--no-obs", action="store_true", help="OBS の録画状態を見ない")
    run.add_argument("--verbose", action="store_true")
    run.set_defaults(handler=command_run)

    subparsers.add_parser("plan", help="何も開かずに準備状況を出す").set_defaults(handler=command_plan)

    devices = subparsers.add_parser("devices", help="デバイス一覧")
    devices.add_argument("--host-api", default="Windows WASAPI")
    devices.add_argument("--json", action="store_true", help="入力と出力の名前を JSON 1 行で出す（GUI 用）")
    devices.set_defaults(handler=command_devices)

    render = subparsers.add_parser("render", help="WAV をオフラインで変換する")
    render.add_argument("--mode", choices=("dsp", "ai", "through"), required=True)
    render.add_argument("--input", required=True)
    render.add_argument("--output", required=True)
    render.add_argument("--semitones", type=float, default=5.0)
    render.add_argument("--formant", type=float, default=1.15)
    render.add_argument("--voice", type=int, default=0)
    render.add_argument("--pitch-offset", type=float, default=0.0)
    render.add_argument("--source-pitch", type=float, default=None)
    render.add_argument("--ai-formant", type=float, default=0.0)
    render.add_argument("--ai-gain", type=float, default=6.0)
    render.set_defaults(handler=command_render)

    listen = subparsers.add_parser("listen-page", help="聴き比べページ（out/listen/index.html）を作る")
    listen.add_argument("--input", action="append", default=[], help="入力の WAV（複数可。既定は自分の声の録音の先頭から）")
    listen.add_argument("--count", type=int, default=2, help="--input を省いたときに使う録音の数")
    listen.add_argument("--output-dir", default="")
    listen.set_defaults(handler=command_listen_page)

    self_pitch = subparsers.add_parser("self-pitch", help="自分の声の高さを測る")
    self_pitch.add_argument("--force", action="store_true", help="保存済みの値を捨てて測り直す")
    self_pitch.set_defaults(handler=command_self_pitch)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    arguments = build_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    sys.exit(main())
