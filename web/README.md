# 監査レビューコンソール（Web デモ・プロトタイプ）

`src/revenue_risk/` のパイプライン出力を可視化する、監査レビュー用の Web UI（静的・依存ライブラリなし）。
**すべて合成データ**。所見は AI による提示であり、確定・是正・通報・開示は独立した人間が判断する（HITL）。
配色は中立のプロフェッショナル（特定企業を想起させない）。

## 画面

| 画面 | 内容 |
|---|---|
| ダッシュボード | 母集団・コストファネル（全件→高リスク）・重要度/アサーション/カテゴリ別・データ品質・エージェント探索・モデル検証（合成） |
| 所見一覧 | 重要度・アサーション・カテゴリ・全文検索で絞り込み。既定は重大＋高 |
| レビューコンソール（HITL） | 取引の事実・カットオフの三角照合・発火ルールとアサーション・ML寄与(SHAP)・収集証憑（インジェクション/矛盾の明示）・推奨手続・人間による確定/棄却 |
| 監査証跡 | WORM＋ハッシュチェーンの整合性・checkpoint・エントリ一覧 |
| レポート | 経営者・監査役向けサマリ |

## 起動

`web/data/data.js`（`window.DEMO_DATA`）を JS 変数として読み込むため、`file://` でもそのまま開けます。

```bash
# 1) デモデータ生成（パイプラインを実走させて web/data/data.js を出力）
python scripts/build_demo_data.py

# 2) （任意）ナレーション音声の生成（高品質ニューラルTTS）
pip install edge-tts
python scripts/generate_narration.py

# 3) 開く（いずれか）
#   - web/index.html をブラウザで直接開く
#   - もしくはローカルサーバ:
python -m http.server -d web 8080   # http://localhost:8080/
```

## デモ動画（ナレーション付き MP4）

`web/demo/demo.mp4` に、各画面を自動操作・遷移しながらアニメーションカーソル・ハイライト・字幕・
**ニューラルTTS音声**で解説する 11 ステップのウォークスルー（H.264/AAC・1600×900）を同梱しています。

再録画（UIやデータを変えた後）:

```bash
pip install edge-tts playwright imageio-ffmpeg
python -m playwright install-deps            # 既存の Edge/Chrome を channel で使用（大きなDL不要）
python scripts/build_demo_data.py            # データ更新時のみ
python scripts/generate_narration.py         # 文面変更時のみ（音声再生成）
python scripts/record_demo_video.py          # web/demo/demo.mp4 を再生成
```

Playwright は導入済みの Microsoft Edge（`channel=msedge`）を利用し、ffmpeg は `imageio-ffmpeg` 同梱の
バイナリを使うため、別途 ffmpeg のインストールは不要です。各ステップは「先頭無音＋ナレーション長」だけ
表示し、同一音声を同期して重ねるため、映像と音声は厳密に一致します。

## ガイドデモ（ブラウザ内・ナレーション付き自動再生）

右上の「ガイド再生」ボタン、または `index.html?demo=1` で自動再生。各画面を自動で操作・遷移しながら、
アニメーションカーソル・ハイライト・字幕・音声で解説します（11ステップ）。

- 音声は `web/data/narration/step-XX.mp3`（edge-tts のニューラル音声）を優先。
- 音声ファイルが無い環境では、ブラウザ内蔵 TTS（Web Speech API）→ 字幕のみの自動送り、へ順にフォールバック。
- 再生中は Space で一時停止/再開、Esc で終了。`prefers-reduced-motion` に配慮。

## 実装メモ

- 依存ライブラリなし（バニラ HTML/CSS/JS）。チャートは SVG を自作。
- ルーティングは URL ハッシュ（`#findings` 等）でディープリンク可能。
- UI/UX レビューと対応は `docs/ux-review.md`。
