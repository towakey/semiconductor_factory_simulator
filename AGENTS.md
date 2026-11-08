# AGENTS

## プロジェクト
半導体工場 操業シミュレーター(Python + Web ビューアー)。設計書は `docs/design.md`。

## ユーザー指示・環境
- 「ブラウザ立ち上げて」と言われたら、`browser_preview` や `start chrome` ではなく、**chrome-devtools MCP で Chrome を開く**こと。ユーザーが画面を共有しながら一緒に作業するため。
  - URL 例: `http://localhost/semiconductor_factory_simulator/web/index.html`
  - 操作は MCP の `new_page` / `evaluate_script` / `click` 等で行う。
- 応答は日本語で。
- XAMPP Apache 上で `http://localhost/semiconductor_factory_simulator/` が動作する(再起動不要・しない)。
- Python は `C:\Users\kyosg\AppData\Local\Programs\Python\Python38\python.exe` を使用(コードは 3.7 互換)。
- テスト: `python -m unittest discover -s tests`。

## ルール
- 検証用の変更は `data/models/small_fab` を `samples/small_fab` と同一に戻すこと。
- `data/runs` の検証 run は最大3個まで残してよい。
