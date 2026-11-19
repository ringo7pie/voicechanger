"""VoiceChanger: マイク入力のリアルタイム ボイスチェンジャー。"""
from .paths import add_addon_lib_to_path

__version__ = "0.2.0"

SAMPLE_RATE = 48000
BLOCK_SIZE = 480  # 10ms

add_addon_lib_to_path()