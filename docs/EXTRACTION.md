# 切り出し対応表

## 対象と由来

対象はゲームリポジトリ内の指定された 5 本の Python スクリプトと `tools/comfyui/` 全体です。他分野のツールの切り出しはこのリポジトリの担当範囲外です。原本は読み取り専用として扱い、変更していません。以下のパスは元リポジトリのルートからの相対パスです。

この実装は既存スクリプトの `urllib.request`、JSON の `prompt` 包装、`prompt_id` による履歴待機、画像メタデータ走査、有限の逐次実行という構成を引き継いでいます。単純なファイル移動ではなく、固定パスとゲーム依存を除いた保守的な抽出・適応です。

## Python スクリプト全件

| 元ファイル | 移行先 / 判断 |
| --- | --- |
| `scripts/run_comfy_prompt_loop.py` | 主な抽出元。`http_json` → `Client.request`、`wait_for_image` → `Client.wait`、`generate_once` と `main` → CLI の `run`。固定ノード ID の編集は `load_workflow` の入力上書きに一般化。`archive_output` のローカル共有フォルダーコピーは `/view` 回収に置換。 |
| `scripts/run_comfy_interior_background_loop.py` | 同一の送信・待機・有限ループを上記へ統合。停止ファイルを継承。背景専用のノード ID、LoRA、UNet、縦横比プリセットと無制限エラー再試行は除外。 |
| `scripts/generate_character.py` | `generate_character` の `POST /prompt` → `GET /history/{id}`、画像候補の逐次生成、履歴記録の考え方を継承。キャラ別名、プロンプト辞書、モデル計画、IP-Adapter 分岐、favorites、採用・復元・Godot アセット配置は除外。新しい一覧は画像回収記録のみで、元の完全な生成履歴とは異なる。 |
| `scripts/generate_character_art.py` | 同じ API 送信・ポーリング経路を統合。職業別プロンプト・固定 seed ノード・ゲーム候補フォルダーへの出力処理は除外。独立した互換ラッパーは作らない。 |
| `scripts/generate_portrait.py` | 同じ API 経路を統合。NPC 辞書、固定 IP-Adapter ワークフロー、`../portraits/` を渡すパス操作は除外。完了未確認でも成功表示する挙動は継承しない。 |
| `tools/comfyui/install_custom_node.py` | 除外。個人環境の自動検出と既存インストール先を再帰削除する更新動作を持つ。今回の CLI はカスタムノード不要なので、破壊的インストーラーを移植しない。 |
| `tools/comfyui/custom_nodes/AdoptSpriteNode/__init__.py` | 任意ノードの移植は今回は行わない。ゲーム候補フォルダーへの直接保存、ファイル名経由の移動、バッチ先頭だけの保存、保存エラーの握りつぶしを含む。標準 `SaveImage` と API 回収で用途を代替。元ノードだけで保存する JSON はそのままでは回収非対応。 |

## ワークフロー・設定全件

以下はすべて除外です。モデル名やカスタムノード、ゲームの美術仕様を持つ JSON を「汎用サンプル」として公開しないためです。

| グループ | 元ファイル | 除外理由 |
| --- | --- | --- |
| カタログ | `tools/comfyui/workflows/character_prompts.json` | キャラクターと用途の固有カタログ。 |
| スプライト | `pixel_art_workflow.json`, `pixel_art_workflow.txt`, `pixel_art_ipadapter_workflow.json`, `background_removal_pixel_workflow.json` | 固有モデル・LoRA・背景除去・参照画像・採用ノードの構成。 |
| 肖像画 | `portrait_workflow.json`, `portrait_ipadapter_workflow.json`, `Hassaku_Portrait_TextBase.json`, `Hassaku_Portrait_IPAdapter.json` | 固有モデル・プロンプト・参照条件の構成。 |
| 画面 / キャラクター | `shop_ui_workflow.json`, `adjutant_ui_workflow.json`, `道具屋.json` | ゲーム画面・登場人物・施設用の構成。 |
| 背景 | `krea2_interior_background_workflow.json`, `krea2_interior_background_prompt.json`, `comfy_shop_ui_pixel_f2_workflow.json`, `comfy_shop_ui_pixel_f2_prompt.json` | 特定のモデル、LoRA、背景用プロンプトを前提にする。 |
| 3D 検証 | `3d test - MultiView.json`, `3d test - MultiView.README.md` | 3D / 多視点の別パイプライン。画像ワークフローの汎用ランナーとは検証範囲が異なる。 |

カタログ以外の上表のファイルも、すべて元の `tools/comfyui/workflows/` 内にあります。新規サンプル `examples/solid-color.api.json` はゲームのワークフローをコピーせず、標準ノード 2 個だけで構成しました。

## 適応で変更した境界

- 個人マシンの出力パスと複数ポート自動探索を除去し、明示的な `--api-url` と `--output-dir` に置換。
- history に ID が現れただけでは成功扱いせず、完了状態と実行エラーを確認。
- 固定回数の sleep ポーリングを単調時計ベースの待機期限と HTTP タイムアウトに置換。
- エラー時の無限再試行は削除。失敗は非ゼロ終了し、ジョブの二重送信を避ける。
- 既定のファイル上書きを禁止し、パス検査・応答サイズ上限・リダイレクト拒否を追加。
- 日本語 README、API 形式サンプル、標準ライブラリの HTTP モックテストを新設。

## 公開前に残る確認

元コードの再配布権限とライセンスは権利者の確認が必要です。AI 支援による抽出は権利関係の確認を代替しません。実 ComfyUI でのサンプル実行と利用者のワークフロー・ノード構成での動作確認も未実施です。生成、インストール、ダウンロード、Git 初期化、コミット、公開操作は行っていません。
