"""OBS の設定（obs-websocket v5）。python -m voicechanger.obs_setup <configure|verify|record>

専用のプロファイルとシーンコレクション「VoiceChanger」を作り、次の入力を置く（既存の「無題」には触らない）:
  声（VoiceChanger）  wasapi_input_capture、device_id は CABLE Output を明示 → トラック 1・3
  ゲームキャプチャ    game_capture（全画面のゲーム、音声もキャプチャ）       → トラック 1・2
  画面キャプチャ      monitor_capture（ゲームを捕まえていないときの予備。音なし）
生のマイクとデスクトップ音声は置かない（knowledge/10-silver/areas/audio-routing.md）。
録画: 詳細モード、MKV、NVENC（H.264）、音声 3 トラック（1=合成、2=ゲーム音、3=声）。

出力モードの切り替えは、プロファイルを有効にし直すまで反映されない（OBS 33.0.1 の OBSBasic_Profiles.cpp）。
configure の後は OBS を閉じて、--profile VoiceChanger --collection VoiceChanger で起動し直す（tools/setup_obs.ps1）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .config import REPO_ROOT
from .obs_client import ObsConnection, load_connection

PROFILE = "VoiceChanger"
COLLECTION = "VoiceChanger"
VOICE_INPUT = "声（VoiceChanger）"
GAME_INPUT = "ゲームキャプチャ"
DISPLAY_INPUT = "画面キャプチャ"
RECORD_DIR = REPO_ROOT / "recordings"
# VoiceChanger の声は、全モードでそろえた遅延 78ms ＋ 出力の溜め 30ms ほど遅れる（推定。実測で直す）
DEFAULT_VOICE_SYNC_OFFSET_MS = -110
TRACKS = {VOICE_INPUT: (1, 3), GAME_INPUT: (1, 2)}

PROFILE_PARAMETERS = [
    ("Output", "Mode", "Advanced"),
    ("AdvOut", "RecType", "Standard"),
    ("AdvOut", "RecFormat2", "mkv"),
    ("AdvOut", "RecTracks", str(1 | 2 | 4)),  # ビットの組: トラック 1・2・3
    ("AdvOut", "RecEncoder", "obs_nvenc_h264_tex"),
    ("AdvOut", "RecAudioEncoder", "ffmpeg_aac"),
    ("AdvOut", "Track1Name", "合成"),
    ("AdvOut", "Track2Name", "ゲーム音"),
    ("AdvOut", "Track3Name", "声"),
    ("AdvOut", "Track1Bitrate", "192"),
    ("AdvOut", "Track2Bitrate", "192"),
    ("AdvOut", "Track3Bitrate", "192"),
]
VIDEO_SETTINGS = {"fpsNumerator": 60, "fpsDenominator": 1, "baseWidth": 1920, "baseHeight": 1080,
                  "outputWidth": 1920, "outputHeight": 1080}
# 録画エンコーダの詳細。OBS を閉じている間に profiles/VoiceChanger/recordEncoder.json へ書く（tools/setup_obs.ps1）
RECORD_ENCODER_SETTINGS = {"rate_control": "CQP", "cqp": 20, "preset2": "p5", "profile": "high"}


def emit(line: str) -> None:
    print(line, flush=True)


def connect() -> ObsConnection:
    connection_info = load_connection()
    if connection_info is None:
        raise SystemExit("data/obs_websocket.json がありません（tools/install_audio_tools.ps1 が書く）")
    connection = ObsConnection(**connection_info)
    connection.connect()
    return connection


def _tracks_body(tracks: tuple[int, ...]) -> dict[str, bool]:
    return {str(number): number in tracks for number in range(1, 7)}


def _pick_item(items: list[dict], *, value: str | None = None, name_prefix: str | None = None) -> dict | None:
    for item in items:
        if value is not None and item.get("itemValue") == value:
            return item
        if name_prefix is not None and str(item.get("itemName", "")).casefold().startswith(name_prefix.casefold()):
            return item
    return None


def configure(connection: ObsConnection, voice_sync_offset_ms: int) -> dict[str, Any]:
    if connection.request("GetRecordStatus").get("outputActive"):
        raise SystemExit("OBS が録画中です。止めてから設定してください")
    result: dict[str, Any] = {}

    profiles = connection.request("GetProfileList")
    if PROFILE in profiles.get("profiles", []):
        if profiles.get("currentProfileName") != PROFILE:
            connection.request("SetCurrentProfile", {"profileName": PROFILE})
    else:
        connection.request("CreateProfile", {"profileName": PROFILE})
    for category, name, value in PROFILE_PARAMETERS:
        connection.request("SetProfileParameter", {"parameterCategory": category, "parameterName": name,
                                                   "parameterValue": value})
    connection.request("SetVideoSettings", VIDEO_SETTINGS)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    connection.request("SetRecordDirectory", {"recordDirectory": str(RECORD_DIR)})

    collections = connection.request("GetSceneCollectionList")
    if COLLECTION in collections.get("sceneCollections", []):
        if collections.get("currentSceneCollectionName") != COLLECTION:
            connection.request("SetCurrentSceneCollection", {"sceneCollectionName": COLLECTION})
    else:
        connection.request("CreateSceneCollection", {"sceneCollectionName": COLLECTION})
    time.sleep(0.5)
    scene = connection.request("GetSceneList")["currentProgramSceneName"]
    existing = {item["inputName"] for item in connection.request("GetInputList").get("inputs", [])}

    # 下から順に重ねる: 画面キャプチャ（予備）→ ゲームキャプチャ → 声
    if DISPLAY_INPUT not in existing:
        connection.request("CreateInput", {"sceneName": scene, "inputName": DISPLAY_INPUT,
                                           "inputKind": "monitor_capture", "inputSettings": {}})
    if GAME_INPUT not in existing:
        connection.request("CreateInput", {"sceneName": scene, "inputName": GAME_INPUT, "inputKind": "game_capture",
                                           "inputSettings": {"capture_mode": "any_fullscreen", "capture_audio": True}})
    else:
        # 捕まえ方（全画面／特定のウィンドウ）は、ゲームに合わせて変えた値を残す。音声だけを有効にする
        connection.request("SetInputSettings", {"inputName": GAME_INPUT, "inputSettings": {"capture_audio": True}})
    result["fitted"] = fit_to_canvas(connection, scene, (DISPLAY_INPUT, GAME_INPUT))
    if VOICE_INPUT not in existing:
        connection.request("CreateInput", {"sceneName": scene, "inputName": VOICE_INPUT,
                                           "inputKind": "wasapi_input_capture", "inputSettings": {}})
    items = connection.request("GetInputPropertiesListPropertyItems",
                               {"inputName": VOICE_INPUT, "propertyName": "device_id"}).get("propertyItems", [])
    cable = _pick_item(items, name_prefix="CABLE Output")
    if cable is None:
        raise SystemExit(f"OBS から CABLE Output が見えません。候補: {[item.get('itemName') for item in items]}")
    connection.request("SetInputSettings", {"inputName": VOICE_INPUT, "inputSettings": {"device_id": cable["itemValue"]}})
    result["voice_device"] = {"name": cable["itemName"], "id": cable["itemValue"]}

    for input_name, tracks in TRACKS.items():
        connection.request("SetInputAudioTracks", {"inputName": input_name, "inputAudioTracks": _tracks_body(tracks)})
    connection.request("SetInputAudioSyncOffset", {"inputName": VOICE_INPUT, "inputAudioSyncOffset": voice_sync_offset_ms})
    for input_name in TRACKS:
        connection.request("SetInputMute", {"inputName": input_name, "inputMuted": False})
    result["special_inputs"] = connection.request("GetSpecialInputs")
    result["reactivated"] = reactivate(connection)
    return result


def fit_transform() -> dict[str, Any]:
    """映像を、縦横比を保ったまま録画の画面（1920x1080）いっぱいに収める（余りは黒の帯）。"""
    return {"positionX": 0.0, "positionY": 0.0, "alignment": 5,  # 5 = 左上
            "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,  # 0 = 中央
            "boundsWidth": float(VIDEO_SETTINGS["baseWidth"]), "boundsHeight": float(VIDEO_SETTINGS["baseHeight"])}


def fit_to_canvas(connection: ObsConnection, scene: str, source_names: tuple[str, ...]) -> list[str]:
    fitted = []
    for item in connection.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", []):
        if item.get("sourceName") in source_names:
            connection.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": item["sceneItemId"],
                                                         "sceneItemTransform": fit_transform()})
            fitted.append(item["sourceName"])
    return fitted


def reactivate(connection: ObsConnection) -> bool:
    """別のプロファイル・コレクションへ一度切り替えて戻す。

    出力モードの変更はプロファイルを有効にし直すまで反映されず、シーンコレクションは切り替え時に保存される
    （OBS 33.0.1）。OBS を閉じて起動し直す方法は、終了が「クラッシュ」と記録されて設定が残らなかったので使わない。
    """
    profiles = connection.request("GetProfileList").get("profiles", [])
    collections = connection.request("GetSceneCollectionList").get("sceneCollections", [])
    other_profile = next((name for name in profiles if name != PROFILE), None)
    other_collection = next((name for name in collections if name != COLLECTION), None)
    if other_profile is None or other_collection is None:
        return False
    connection.request("SetCurrentSceneCollection", {"sceneCollectionName": other_collection})
    connection.request("SetCurrentSceneCollection", {"sceneCollectionName": COLLECTION})
    connection.request("SetCurrentProfile", {"profileName": other_profile})
    connection.request("SetCurrentProfile", {"profileName": PROFILE})
    time.sleep(0.5)
    return True


def verify(connection: ObsConnection) -> dict[str, Any]:
    """設定が意図どおりかを調べる。問題は problems に並べる。"""
    problems: list[str] = []
    report: dict[str, Any] = {}
    profile = connection.request("GetProfileList").get("currentProfileName")
    collection = connection.request("GetSceneCollectionList").get("currentSceneCollectionName")
    report["profile"], report["collection"] = profile, collection
    if profile != PROFILE:
        problems.append(f"プロファイルが {profile}（{PROFILE} で起動していない）")
    if collection != COLLECTION:
        problems.append(f"シーンコレクションが {collection}")
    parameters = {}
    for category, name, expected in PROFILE_PARAMETERS:
        value = connection.request("GetProfileParameter", {"parameterCategory": category,
                                                           "parameterName": name}).get("parameterValue")
        parameters[f"{category}.{name}"] = value
        if value != expected:
            problems.append(f"{category}.{name} が {value!r}（期待 {expected!r}）")
    report["parameters"] = parameters
    video = connection.request("GetVideoSettings")
    report["video"] = video
    for key, expected in VIDEO_SETTINGS.items():
        if video.get(key) != expected:
            problems.append(f"映像の {key} が {video.get(key)}（期待 {expected}）")
    report["record_directory"] = connection.request("GetRecordDirectory").get("recordDirectory")
    special = connection.request("GetSpecialInputs")
    report["special_inputs"] = special
    if any(value for value in special.values()):
        problems.append(f"常設の音声入力がある（既定デバイスを追う）: {special}")
    inputs = {item["inputName"]: item["inputKind"] for item in connection.request("GetInputList").get("inputs", [])}
    report["inputs"] = inputs
    for name, kind in ((VOICE_INPUT, "wasapi_input_capture"), (GAME_INPUT, "game_capture"),
                       (DISPLAY_INPUT, "monitor_capture")):
        if inputs.get(name) != kind:
            problems.append(f"入力 {name}（{kind}）が無い")
    for kind in ("wasapi_input_capture", "wasapi_output_capture"):
        for name, input_kind in inputs.items():
            if input_kind == kind and name != VOICE_INPUT:
                problems.append(f"想定外の音声入力 {name}（{kind}）。生のマイクやデスクトップ音声を入れない")
    if VOICE_INPUT in inputs:
        settings = connection.request("GetInputSettings", {"inputName": VOICE_INPUT}).get("inputSettings", {})
        items = connection.request("GetInputPropertiesListPropertyItems",
                                   {"inputName": VOICE_INPUT, "propertyName": "device_id"}).get("propertyItems", [])
        chosen = _pick_item(items, value=settings.get("device_id"))
        report["voice_device"] = chosen.get("itemName") if chosen else settings.get("device_id")
        if not chosen or not str(chosen.get("itemName", "")).startswith("CABLE Output"):
            problems.append(f"声の入力の機器が CABLE Output でない: {report['voice_device']}")
        report["voice_sync_offset_ms"] = connection.request(
            "GetInputAudioSyncOffset", {"inputName": VOICE_INPUT}).get("inputAudioSyncOffset")
    if GAME_INPUT in inputs:
        settings = connection.request("GetInputSettings", {"inputName": GAME_INPUT}).get("inputSettings", {})
        report["game_capture"] = {key: settings.get(key) for key in ("capture_mode", "window", "capture_audio")}
        if not settings.get("capture_audio"):
            problems.append("ゲームキャプチャの音声が無効")
    tracks = {}
    for name, expected in TRACKS.items():
        if name in inputs:
            actual = connection.request("GetInputAudioTracks", {"inputName": name}).get("inputAudioTracks", {})
            tracks[name] = [number for number, enabled in sorted(actual.items()) if enabled]
            if tracks[name] != [str(number) for number in expected]:
                problems.append(f"{name} のトラックが {tracks[name]}（期待 {list(expected)}）")
    report["tracks"] = tracks
    report["problems"] = problems
    return report


def record(connection: ObsConnection, seconds: float) -> dict[str, Any]:
    if connection.request("GetRecordStatus").get("outputActive"):
        raise SystemExit("既に録画中です")
    connection.request("StartRecord")
    deadline = time.monotonic() + 10
    while not connection.request("GetRecordStatus").get("outputActive"):
        if time.monotonic() > deadline:
            raise SystemExit("録画が始まりません")
        time.sleep(0.2)
    time.sleep(seconds)
    response = connection.request("StopRecord")
    return {"output_path": response.get("outputPath")}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m voicechanger.obs_setup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure_parser = subparsers.add_parser("configure")
    configure_parser.add_argument("--voice-sync-offset-ms", type=int, default=DEFAULT_VOICE_SYNC_OFFSET_MS)
    subparsers.add_parser("verify")
    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--seconds", type=float, default=10.0)
    subparsers.add_parser("encoder-json", help="recordEncoder.json に書く内容を出す")
    arguments = parser.parse_args(argv)
    if arguments.command == "encoder-json":
        emit(json.dumps(RECORD_ENCODER_SETTINGS))
        return 0
    connection = connect()
    try:
        if arguments.command == "configure":
            emit("OBS_CONFIGURED " + json.dumps(configure(connection, arguments.voice_sync_offset_ms), ensure_ascii=False))
            return 0
        if arguments.command == "verify":
            report = verify(connection)
            emit(json.dumps(report, ensure_ascii=False, indent=2))
            emit(f"OBS_VERIFY problems={len(report['problems'])}")
            return 0 if not report["problems"] else 5
        if arguments.command == "record":
            emit("OBS_RECORDED " + json.dumps(record(connection, arguments.seconds), ensure_ascii=False))
            return 0
    finally:
        connection.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
