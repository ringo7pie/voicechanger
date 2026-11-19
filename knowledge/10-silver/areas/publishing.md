# publishing

リポジトリの公開先と公開の決まり。

- GitHub の `ringo7pie/voicechanger`（public）で公開する（2026-11-19 に作成）。git の remote 名は `github`。
  `origin` は NAS（`\\192.168.8.1\usb-hdd\NKGIT\nkdx\Games\VoiceChanger\VoiceChanger.git`）のまま。push は両方へ行う。
- ライセンスは GPL-3.0-or-later（`LICENSE`）。依存の pedalboard（GPLv3）と obsws-python（GPL-3.0-only）に合わせた。
- CLAUDE.md と `knowledge/` も公開リポジトリに含める（2026-11-19 にユーザーが選んだ）。
- README の画像は `docs/images/`。GUI の `--self-test --screenshot-dir <dir>` が保存する `mode_dsp.png` / `mode_ai.png` を写して使う。
- Beatrice のモデル（`.bin`）と画像は規約で再頒布不可。公開リポジトリにも入れない。
