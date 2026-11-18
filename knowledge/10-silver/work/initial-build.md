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
  残り: 2 折り返し試験・連続運転、3 OBS 設定・試し録り、4 実マイク確認・仕上げ。
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
