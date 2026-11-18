"""音声デバイスの列挙と選択（sounddevice / PortAudio の WASAPI）。

既定デバイスは使わない。VB-CABLE の導入で Windows の既定デバイスが変わることがあり、
既定を追うと声とゲーム音が混ざるため（knowledge/10-silver/areas/audio-routing.md）。
"""
from __future__ import annotations

from dataclasses import dataclass


class DeviceNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float


def list_devices(host_api_name: str = "Windows WASAPI") -> list[AudioDevice]:
    import sounddevice

    host_apis = sounddevice.query_hostapis()
    api_indices = [index for index, api in enumerate(host_apis) if api["name"] == host_api_name]
    if not api_indices:
        raise DeviceNotFoundError(f"ホスト API {host_api_name!r} がありません（{[api['name'] for api in host_apis]}）")
    devices = []
    for index, device in enumerate(sounddevice.query_devices()):
        if device["hostapi"] == api_indices[0]:
            devices.append(AudioDevice(index, device["name"], int(device["max_input_channels"]),
                                       int(device["max_output_channels"]), float(device["default_samplerate"])))
    return devices


def default_device_names(host_api_name: str = "Windows WASAPI") -> dict[str, str | None]:
    """Windows の既定の録音・再生デバイスの名前（PortAudio を初期化した時点のもの）。"""
    import sounddevice

    host_apis = sounddevice.query_hostapis()
    api = next((api for api in host_apis if api["name"] == host_api_name), None)
    names: dict[str, str | None] = {"input": None, "output": None}
    if api is None:
        return names
    for kind, key in (("input", "default_input_device"), ("output", "default_output_device")):
        index = api.get(key, -1)
        if index is not None and index >= 0:
            names[kind] = sounddevice.query_devices(index)["name"]
    return names


def find_device(name: str, kind: str, host_api_name: str = "Windows WASAPI",
                devices: list[AudioDevice] | None = None) -> AudioDevice:
    """名前で探す。完全一致を優先し、無ければ部分一致（大文字小文字は区別しない）。候補が複数なら誤りにする。"""
    if kind not in ("input", "output"):
        raise ValueError("kind は input か output")
    candidates = [device for device in (devices if devices is not None else list_devices(host_api_name))
                  if (device.max_input_channels if kind == "input" else device.max_output_channels) > 0]
    wanted = name.casefold()
    exact = [device for device in candidates if device.name.casefold() == wanted]
    if exact:
        return exact[0]
    partial = [device for device in candidates if wanted in device.name.casefold()]
    if len(partial) == 1:
        return partial[0]
    names = [device.name for device in candidates]
    if not partial:
        raise DeviceNotFoundError(f"{kind} デバイス {name!r} が見つかりません。候補: {names}")
    raise DeviceNotFoundError(f"{kind} デバイス {name!r} に合うものが複数あります: {[device.name for device in partial]}")
