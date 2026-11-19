# installer

インストーラー版（PyInstaller の onedir ＋ Inno Setup 6）の作り方と落とし穴。

- 作り方: `pwsh -NoProfile -File .\tools\build_installer.ps1`（2026-11-19 の実測で約 105 秒、`out\installer\VoiceChanger-Setup-<版>.exe` は 40MB）。
  版は `voicechanger\__init__.py` の `__version__`。PyInstaller 6.22.3 は `.venv`（`requirements-build.txt`）、
  Inno Setup 6.7.3 はユーザー単位で `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` に入っている。
- 凍結版の書き込み先は `%LOCALAPPDATA%\VoiceChanger`（`data\`・`runtime\`）、ソース版はリポジトリのまま。
  環境変数 `VOICECHANGER_DATA_DIR` で上書きできる。凍結版の試験はこれを scratchpad に向け、本物のデータを汚さない。
- 凍結版のエンジンは `VoiceChanger.exe engine <コマンド>`。窓アプリなので stdout が無い。デバイス一覧は
  `engine devices --json --json-out <file>` でファイルに受け取る。部品は `engine addons --pylibrb --beatrice-zip <zip>` でも入る。
- 試し方: `/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER /NORESTART /DIR=<scratchpad> /MERGETASKS="!desktopicon" /LOG=<file>` で
  UAC なしに入れ、`--self-test` を流し、`unins000.exe /VERYSILENT` で消す（2026-11-19 に全段合格・アンインストールを確認）。
- 2026-11-19 に、v0.2.0 のインストーラーが他の PC で動作したとユーザーが報告した（試した機能の範囲は聞いていない）。
- pylibrb は GPL-2.0（or-later の表明なし）なので同梱しない。GUI の「追加の部品」か `engine addons --pylibrb` で
  PyPI の cp312-abi3 wheel（URL と SHA-256 は `voicechanger\addons.py` に固定）を `runtime\lib` に入れる。
  dsp モードは pylibrb が無いと使えない（numpy の代替は無い）。`build_installer.ps1` は pylibrb が混ざったら止まる。
- VB-CABLE は規約上 AS IS で同梱できる。条件は「www.vb-cable.com」と「VB-CABLE is a donationware, all participations are welcome」の明記
  （https://vb-audio.com/Services/licensing.htm）。無人インストールの引数は付属 readme に無いので、セットアップ画面を出して利用者に押してもらう。
- VB-CABLE の導入済みの判定は `C:\Windows\System32\DriverStore\FileRepository\vbmmecable64_*` の有無。
  `System32\drivers` に `vbaudio_cable*.sys` は無かった（2026-11-19）。Inno Setup の非管理者モードでもこの判定は通った。
- ソース版のエンジンが 8765 を使っている間に、データの置き場所が違う凍結版を起動すると、bind が WinError 10013 で失敗した（2026-11-19）。
  今はポートが使えなければ空きポートで起動する。GUI と `Run.ps1` は `control.json` の port / url を読む。
- `config\voicechanger.json` の `self_voice_dir` は null（個人のパスを公開しないため）。自分の声の録音を使う試験は、環境変数
  `VOICECHANGER_SELF_VOICE_DIR=E:/nkdx/Games/voice_train/corpora/self/trimmed` を入れないと skip される（入れると全件通る）。
