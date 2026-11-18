# VoiceChanger

YouTube 用のゲームプレイ録画で、マイクの声をリアルタイムに変える。
マイク → 自作エンジン（`voicechanger/`、Python 3.14 の venv）→ VB-CABLE（仮想マイク「CABLE Output」）→ OBS Studio。
変換は 2 方式を切り替える: `dsp`（ピッチ・フォルマント変換）と `ai`（Beatrice 2 公式 VST3 を pedalboard で読む。CPU）。
素の声を録画に入れないことを最優先にする。

## git

- 管理外: `.venv/` `runtime/` `data/` `out/` `recordings/`、`tools/claude_progress.*`
- Beatrice のモデル（`.bin`）と画像は規約で再頒布不可。コミットしない。

## 構成

| パス | 内容 |
|---|---|
| `Run.ps1` | 起動・停止・状態・モード切替（ターミナル用） |
| `VoiceChanger.cmd` | GUI を開く（`.venv\Scripts\pythonw.exe -m voicechanger.gui`。デスクトップのショートカットも同じ） |
| `voicechanger/` | エンジン（入出力・変換・操作 API） |
| `voicechanger/gui/` | GUI（PySide6）。エンジンは別プロセスで起動し、操作 API で操作する |
| `config/` | 既定の設定、取得物の URL・版・SHA-256 |
| `tools/` | 導入・確認・OBS 設定・試験のスクリプト |
| `tests/` | unittest |
| `knowledge/` | ナレッジベース本体 |

## ビルドとテスト

- 依存の導入: `tools\install_engine_deps.ps1`（ユーザーが `!` で実行）
- テスト: `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .`（リポジトリのルートで実行）
- GUI の通し試験（WAV 入力・耳に聞こえない出力。数十秒ウィンドウが出る）:
  `.\.venv\Scripts\python.exe -m voicechanger.gui --self-test --input-wav <WAV> --output-device "スピーカー (Steam Streaming Speakers)"`

## 常時ロードされる知識

@knowledge/10-silver/areas/env.md
@knowledge/10-silver/areas/audio-routing.md
@knowledge/10-silver/work/index.md
