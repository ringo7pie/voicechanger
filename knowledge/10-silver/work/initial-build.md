# initial-build — ボイスチェンジャーの初回構築

plan: `C:\Users\mt\.claude\plans\youtube-curious-twilight.md`

## 状態（2026-11-17 20:00）

- 段階 A のタスク 1〜8 が完了した。全 49 試験が通る。
- 聴き比べページ `out\listen\index.html` を生成した。ユーザーはまだ聴いていない。
- 2026-11-17 22:40 に GUI ツール（`voicechanger\gui`、PySide6）を作った。デスクトップに `VoiceChanger.lnk`（`F:\users\mt\Desktop`）。試験 79 件が通る。
  - 確かめたこと: GUI の自己試験（開始 → dsp / ai / mute → スライダ → 停止）、親プロセスが消えるとエンジンが 1.1 秒で止まる、
    状態取得を続けた 2 分の負荷（途切れ 0、最悪 4.48ms）、二重起動の防止。
  - ユーザーの実マイクとヘッドホンでの確認は未実施。
- ユーザーは実マイクで GUI を試し、Snipping Tool のマイクには地声が入ると報告した（物理マイクを録るため。`areas/audio-routing.md`）。
- 2026-11-17 23:03 に `install_audio_tools.ps1` が成功した（VB-CABLE: デバイス確認済み、OBS 33.0.1: `/S` の無人インストールで入った、
  websocket と `user.ini` を書いた）。再起動はまだ。
- 導入直後の比較で、既定の再生（通常・マルチメディア）が VP32AQ から CABLE Input に変わっていた。
- 2026-11-17 23:15 に、ユーザーが mmsys.cpl で既定デバイスを設定し、読み取りで確かめた（意図どおり）:
  再生（通常・マルチメディア・通信）＝VP32AQ (NVIDIA High Definition Audio)、録音（通常・マルチメディア・通信）＝CABLE Output (VB-Audio Virtual Cable)。
  `data\default_devices_before.json` との比較では録音の 3 役割が「変わった」と出る（終了コード 5）が、これは意図した変更。
- `tools\check_audio_devices.ps1` の注意は、再生の既定が CABLE のときだけ出すように直した（要約に `cable_is_render_default` を追加）。
- 再起動後、既定の再生が CABLE Input に戻っていたので、ユーザーが mmsys.cpl で VP32AQ に戻した
  （読み取りで確認: 再生＝VP32AQ ×3、録音＝CABLE Output ×3）。
- ユーザーが GUI（出力＝CABLE Input）を動かしたまま Snipping Tool でマイク付き録画をし、変換後の声が入ることを確かめた。
  最初の録画が無音だったのは、ツールバーのマイクがオフだったため。CABLE Output には変換後の声が届いていた
  （Claude が音量だけを 3 秒測った: 295/298 ブロックで音あり、最大 -1.0dB）。
- 段階 B の進捗表示（`.\tools\claude_progress.ps1`）でタスク 1（再起動後の確認）が完了。
- 2026-11-18 に OBS の設定（段階 B のタスク 3）が完了: `tools\setup_obs.ps1`・`start_obs.ps1`・`record_trial.ps1`、`voicechanger\obs_setup.py`。
  verify の問題 0、試し録り合格（H.264 1920x1080 60fps、MKV、音声 3 トラック）。試験 85 件が通る。試し録りは `recordings\2026-11-18 09-48-13.mkv`。
- 2026-11-18 11:10: ユーザーの初の実録画（`recordings\2026-11-18 10-53-45.mkv`）でキャラの声が変換されていた。原因は既定の再生がスピーカーだったこと、
  ゲームキャプチャが窓のゲームを捕まえていなかったこと。既定の再生をヘッドホンにし（ユーザー）、ゲームキャプチャを窓指定・映像を 1920x1080 に収めた。
  試験 87 件（2 件はエンジン起動中で飛ばす）。setup_obs.ps1 の流し直しでゲームが固まり、ユーザーが自分で終了させる。
  次: ゲーム再起動後、ゲーム音が鳴る場面で話しながら 20〜30 秒録ってもらい、トラック 2 にゲーム音、トラック 3 にキャラの声が無いことを確かめる。
- 2026-11-18 11:12 の録画（`recordings\2026-11-18 11-12-58.mkv`）で、声は変換、ゲームの声は変換されずにゲーム音のトラックに入った（ユーザー確認）。
  有線ヘッドホンの音漏れをマイクが拾い、ゲームの声が小さく変換されて声のトラックにも入っていた（包絡の相関 0.45、声が 170ms 遅れ）。
  ユーザーが Bluetooth イヤホン「J33」に替えて解消した。今の既定は再生 3 役割＝ヘッドホン (J33)、通信の録音＝ヘッドセット (J33)（ユーザーはこのままでよいとした）。
  areas/audio-routing.md の既定デバイスの記述は、ユーザーの判断で書き換えていない。
  残り: 同期補正 -110ms の実測、無音モードの漏れ確認、30 分の連続運転、README の仕上げ、archive-work。全体の音量 +20dB は下げる案内済み。
- 残り（タスク 4・2）: 無音モードで話して声のトラックに漏れないかの確認、OBS で実際のゲームを録る確認、
  同期補正 -110ms（推定値）を実測で直す、折り返し試験と 30 分の連続運転（未実施）、README の仕上げ、archive-work。
- ユーザーの保存値は全体の音量 +20dB のままで、声が上限に張り付く（下げるよう案内した）。

## 再開手順（段階 B）

進捗は、再開時に `.\tools\claude_progress.ps1 -Init` で 4 タスク（9〜12）を登録し直してから付ける。

- タスク 9: 再起動後の確認
  - `pwsh -NoProfile -File .\tools\check_audio_devices.ps1 -ExpectCable` と `.\Run.ps1 -Plan` を実行し、既定が上の設定のままか確かめる
    （再生の既定が CABLE に戻っていたら mmsys.cpl で直すよう案内する。自動では変えない）。
  - GUI の出力を CABLE Input に切り替えてもらう。GUI の出力の初期値は「保存した出力 → CABLE Input → ヘッドホン」で、保存値はヘッドホン。
  - 済み: Snipping Tool（Print Screen の録画）のマイク付き録画に変換後の声が入ることを、ユーザーが確かめた（上の「状態」）。
- タスク 10: `tools\test_loopback.ps1` を作る（未作成）
  - エンジンを WAV 入力で裏起動し、CABLE Output を sounddevice で録る。無音でないことと遅延（相互相関）を測る。
  - 30 分の連続運転で、underruns・drift の回数と CPU を記録する。
- タスク 11: `tools\start_obs.ps1`・`setup_obs.ps1`・`record_trial.ps1` を作る（未作成）
  - 手順と websocket のリクエストは plan の「OBS」節にある。
  - `voicechanger\obs_client.py` の `ObsConnection` を使う。
- タスク 12: 実マイクでの確認、README の仕上げ（使い方・権利と注意）、update-facts、archive-work。

## 未決

- ユーザーが GUI で試した値が `data\settings.json` に残っている（出力の音量 +20dB、ピッチ +12、フォルマント ×2.0、AI の微調整 +12 など、上限まで動かした値）。

- 聴き比べで、プリセット、AI の声、ピッチの微調整を選んでもらう。Beatrice の出力が約 2 半音低い件は、ここで判断する。
