# env

<!-- 常時ロードされる。50 行以内。areas/ は 2 ファイルまで。 -->
<!-- 終わりのない責任領域と、知らないと無駄に失敗する制約だけを書く。 -->
<!-- 終わりがある作業は work/ へ。外部資料の要約は resources/ へ。 -->
<!-- 断定文で書く。コードから導出できる情報（構成・依存一覧）は書かない。 -->

## 実行環境

- グローバルの Python は `E:\Program Files\Python314\python.exe`（3.14.7。2026-11-17 時点で site-packages は pip のみ）。フルパスで呼ぶ。
  グローバルには何も入れず、リポジトリの `.venv` を使う。
- GPU は RTX 3070（VRAM 8GB）。DungeonQuest のゲームと音声サーバー 3 つの同時起動で 7.85GB 使用・空き 0.35GB の記録がある
  （`E:\nkdx\Games\DungeonQuest\knowledge\10-silver\areas\env.md` 18 行）。録画の NVENC も VRAM を使う。
  声の変換は GPU を使わず CPU で行う。

## 落とし穴

- Claude Code の PowerShell ツールは Job の中で動き、裏で起動した子孫プロセスはツールの終了時に片付けられる。
  常駐させる OBS とエンジンは `Invoke-CimMethod -ClassName Win32_Process -MethodName Create` で Job の外に起動する
  （`E:\nkdx\Games\ChatAI\knowledge\10-silver\areas\env.md` 16 行）。エンジンは `Run.ps1 -Background` がこれを行う。
- Beatrice の配布サーバー（storage.prj-beatrice.com）は、スクリプトからの取得を HTTP 403 で拒否する（HEAD も Range 付き GET も）。
  VST とモデルの zip はユーザーがブラウザで取得してリポジトリの `Downloads\` に置き、`tools\install_engine_deps.ps1` が照合・展開する。
- pedalboard（JUCE）で Beatrice の VST3 を読むときは、バンドル内の DLL 本体
  （`...\beatrice_2.0.0-rc.3.vst3\Contents\x86_64-win\beatrice_2.0.0-rc.3.vst3`）を渡す。
  バンドルのフォルダを渡すと `unsupported plugin format or scan failure` で失敗する。
- 単体試験はリポジトリのルートで `.\.venv\Scripts\python.exe -m unittest discover -s tests -t .` を実行する。`-t .` が無いと import が通らない。
- PortAudio のデバイス番号は起動ごとに変わる（マイクが [12] から [18] になった）。デバイスは名前で選ぶ。
- 耳に聞こえない出力先として「スピーカー (Steam Streaming Speakers)」を試験に使える（`Run.ps1 -OutputDevice`）。
- obsws_python は接続時に INFO レベルでパスワードを含む行をログに出す（`baseclient.py`）。
  使うときは `logging.getLogger("obsws_python")` を CRITICAL にする。
- Windows の venv の `.venv\Scripts\python.exe` と `pythonw.exe` は中継役で、本物の Python を子プロセスとして起動する。
  Popen や Start-Process で得た PID は、エンジンや GUI の本体の PID と一致しない。
  エンジンは `data\control.json` に `parent_pid`（`--parent-pid` の値）を書き、GUI はそれで自分が起動したエンジンを見分ける。
  ウィンドウを閉じる要求（CloseMainWindow）は子の本体に送る。中継役を強制終了すると本体も後始末なしに止まり、`control.json` が残る（PID の確認で無視される）。
- 試験でエンジンや GUI を動かすときは `run --settings-file` を使い、本物の `data\settings.json` を書き換えない
  （GUI の `--self-test` は自動で `data\self_test_settings.json` を使う）。2026-11-17 に自己試験が本物の保存値を書き換えたので戻した。
  `--self-test` は開始時に本物の `settings.json` を写すので、`--screenshot-dir` の画面には本物の保存値が出る。
- status の `output_peak_db` は瞬間値ではなく、ピークを保持して 1 ブロック（10ms）ごとに 1.5dB 下げるメーター値（`engine.py`）。
  モードを切り替えた直後は前のモードの値が残る（0dB から -90dB まで約 0.6 秒）。無音の判定は切り替えの 1 秒後から測る
  （2026-11-19 に自己試験の mute 段がこれで -61〜-89dB を拾って不合格になっていたのを直した）。
- 公開先と公開の決まりは `knowledge/10-silver/areas/publishing.md` を Read する。
- インストーラー・凍結版・追加の部品・データの置き場所を触るとき、自分の声の録音を使う試験を流すときは
  `knowledge/10-silver/areas/installer.md` を Read する。
- OBS 33.0.1 を websocket で設定した後に CloseMainWindow で閉じて起動し直すと、「OBS Studio のクラッシュが検出されました」が出て
  （`--disable-shutdown-check` を付けても出た）、シーンコレクションの入力と出力の設定（Output.Mode・RecFormat2・RecTracks・RecEncoder）が
  保存されていなかった（トラック名・映像・録画先は残った）。設定は動いている OBS のまま、プロファイルとシーンコレクションを一度別のものへ
  切り替えて戻して反映・保存する（`voicechanger\obs_setup.py` の `reactivate`、`tools\setup_obs.ps1`）。
  この確認画面ではユーザーに「通常モード」を選んでもらう（セーフモードでは websocket が動かない）。
- ゲームキャプチャがゲームを捕まえている間は `tools\setup_obs.ps1`（reactivate で付け直しと映像の作り直しが起きる）を流さない。ゲームを閉じてから流す。
  2026-11-18 11:09:15 に Demon of the Crown（D3D12・Vulkan・GL を hook 中）を捕まえたまま流した直後に、ゲームが「応答なし」で固まった
  （ゲームの 10 秒おきの問い合わせが 11:09:09 で途切れた。因果は推測）。コードでの防止はユーザーが不要とした。
- PowerShell 7 の `Start-Process -ArgumentList` に配列を渡すと、空白を含む要素が引用符なしでつながれて引数が分断される。
  空白を含む要素は自分で引用符で囲んだ 1 本の文字列にして渡す。
- PySide6 6.12.0（cp310-abi3 の wheel）は Python 3.14.7 で動き、既定のスタイルで Windows のダーク／ライトに追従する。
- Beatrice と Rubber Band の詳細（状態の形式・遅延・ピッチの式）を触るときは `knowledge/10-silver/resources/voice-engines.md` を Read する。
