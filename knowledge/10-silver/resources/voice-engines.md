# 声の変換エンジンの仕様と実測

外部の部品（Beatrice 2 公式 VST、Rubber Band）について、ソースと実測で確かめたこと。2026-11-17 時点。
実測の環境は Ryzen 9 3900X、48kHz、10ms（480 サンプル）ブロック。

## Beatrice 2 公式 VST 2.0.0-rc.3（pedalboard 0.9.26 で読む）

### 状態とモデル

- モデルの指定は VST のパラメータではなく、状態の中の文字列。
- pedalboard の `raw_state` は `b"VC2!"` + uint32 長さ（NUL を除く XML のバイト数）+ XML + NUL。
- XML の `<IComponent>` は JUCE 独自の base64。`"<バイト数>."` の後に、データを little-endian の整数とみて
  下位から 6bit ずつ `.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+` の文字にしたもの。
- 復号した中身は int32 サイズ + {int16 ID, int32 型（0:int、1:double、2:文字列＝int32 長さ + UTF-8）, 値} の繰り返し。ID 1 が Model（TOML の絶対パス）。
- ID 1 を差し替えて `raw_state` に入れるとモデルが読み込まれる。読み込みは約 1.2 秒、無音でないことの検証を含めて約 3 秒。
- モデルが未設定のときや読み込みに失敗したときは、完全な無音を出す。

### パラメータ（pedalboard での名前）

| 名前 | 内容 |
|---|---|
| `voice` | `"ID 0"`〜`"ID 256"`。話者数と同じ番号はモーフィング |
| `pitch_shift_st` | ±24 半音、1/8 刻み |
| `formant_shift_st` | ±2 半音、0.5 刻み |
| `average_source_pitch` | 0〜128（MIDI ノート番号の単位）、既定 52 |
| `lock`、`output_gain_db` | ロックの対象、出力の音量 |
| `latency_reporting` | 既定 `"Off — Responsive"`。このとき `reported_latency_samples` は 0 |

- ホストから `voice` を変えても `pitch_shift_st` は連動しない。
  GUI と同じ式 `PitchShift = clamp(平均目標ピッチ + FormantShift − AverageSourcePitch, ±24)` をこちらで計算する。
- 公式モデル 1 の声と平均目標ピッチ: 0 つくよみちゃん (UTAU) 65.625、1 つくよみちゃん (コーパス) 63.5、2 刻鳴時雨 54.0、3 OLUNE 50.875。

### 実測

- 実時間比は約 0.11、10ms ブロックの最悪は約 4.9ms。
- 中身の遅延は、実声で 1680〜1776 サンプル（35〜37ms）。
- 出力の音量の出入りは、入力に遅れなく付く。音量が一定の合成音で包絡の相互相関をとると遅延 0 と出るので、遅延は実声で測る。
- 出力は入力より約 10dB 小さい。`output_gain_db` で補う。
- 自分の声（103Hz、MIDI 43.9）から式どおりにシフトしても、出力のピッチは各声の平均目標より約 2 半音低く測れた。原因は確定していない。

### ライセンス

- VST のコードは MIT。同梱の非公開の推論ライブラリは、Project Beatrice の許諾なしに、法で認められた範囲を超えて利用できない。
- VST の zip には JVS のモデル（`beatrice_paraphernalia_jvs`）も入っているが、使わない。

## Rubber Band（pylibrb 0.1.2。Python 3.14 では cp312-abi3 の wheel で動く）

- pylibrb 0.1.2 のメタデータの License は「GPLv2」で、同梱の LICENSE は GPLv2 の本文だけ。「or later」かは確認できていない
  （2026-11-19）。リポジトリ（GPL-3.0-or-later）には同梱せず、任意の依存のままにする。

- `formant_scale` は R3（`ENGINE_FINER`）と `FORMANT_PRESERVED` の組み合わせのときだけ効く。
  意味は「ピッチで動いた後の包絡に掛ける倍率」で、0 は自動（= 1 / pitch_scale）。
  元の声に対する倍率 F は `formant_scale = F / pitch_scale` に換算する。
- R3 + `WINDOW_SHORT` + `PROCESS_REALTIME` で 10ms ブロックを流すと、実時間比は 0.02〜0.08。
- 出力 FIFO の溜め量が 256 のときの遅延は、−12 半音で 78ms、0 半音で 60ms、+12 半音で 49ms（自分の声 30 秒、包絡の相互相関）。
- −24 半音では、溜め量 256 でも取りこぼしが出た。範囲は ±12 半音に限る。

## ピッチ推定

- 自作のピッチ推定は YIN を使う。不偏補正つきの正規化自己相関は、150Hz の合成音を 112Hz と誤った。
