# audio-routing

<!-- 常時ロードされる。50 行以内。areas/ は 2 ファイルまで。 -->
<!-- 終わりのない責任領域と、知らないと無駄に失敗する制約だけを書く。 -->
<!-- 終わりがある作業は work/ へ。外部資料の要約は resources/ へ。 -->
<!-- 断定文で書く。コードから導出できる情報（構成・依存一覧）は書かない。 -->

音声の経路と、素の声を録画に漏らさないための規則。

- 生のマイクを OBS に登録しない。OBS へ入る声は CABLE Output だけにする。
- OBS の音声入力（`wasapi_input_capture` / `wasapi_output_capture`）は `device_id` の既定値が `"default"` で、
  Windows の既定デバイスを追う（OBS 33.0.1 の `plugins/win-wasapi/win-wasapi.cpp`）。
  VB-CABLE の導入で既定デバイスが変わると声とゲーム音が互いのトラックに混ざるので、`device_id` は必ず明示する。
- 確認用の素の声モード（`through`）とモニター出力は保存しない。OBS の録画中は拒否する。
- 変換で例外が出たら、素の声ではなく無音を出す。
- 他のアプリ（Print Screen の録画＝Snipping Tool、Discord など）が物理マイクを録ると、VoiceChanger の動作中でも地声が入る
  （2026-11-17 にユーザーが報告）。VoiceChanger は変換後の声を選んだ出力デバイスにだけ流し、物理マイクの音は変えられない。
  変換後の声を他のアプリに渡すには、出力を CABLE Input にし、録るアプリのマイクを CABLE Output にする。
  マイクを選べないアプリ（Snipping Tool は機器を選ぶ画面を公式情報で確認できなかった）には、Windows の既定の録音デバイスを CABLE Output にする。
  既定の再生デバイスは CABLE にしない。エンジンの入力は物理マイクを名前で指定しているので、既定の録音デバイスを CABLE Output にしても音は回らない。
- Snipping Tool（Print Screen の録画）のマイクは、Windows の既定の録音デバイスを録る。既定の録音を CABLE Output、VoiceChanger の出力を
  CABLE Input にすると、変換後の声が録画に入る（2026-11-17 にユーザーが確認）。マイクは録画のツールバーのアイコンでオンにする
  （斜線ありだとマイクを録らず、録画は無音になる）。
- VB-CABLE Driver Pack46 を入れると、Windows の既定の再生デバイス（通常とマルチメディア）が CABLE Input に変わる
  （2026-11-17 に実測。通信用の再生と録音の既定は変わらなかった）。`tools\check_audio_devices.ps1 -CompareBaseline` で見つけ、
  mmsys.cpl でユーザーに戻してもらう。導入で「CABLE In 16ch (VB-Audio Virtual Cable)」という再生デバイスも増える。
- 導入直後に戻しても、PC を再起動すると既定の再生が 3 役割（通常・マルチメディア・通信）とも CABLE Input に戻った（2026-11-17 に実測）。
  録音の既定（CABLE Output）は残った。GUI はデバイス一覧を読むときに、既定の再生が CABLE なら赤い警告を出す（`default_render_warning`）。
  入力が CABLE Output のときも、音が回り続けるので警告する（`input_loop_warning`）。
