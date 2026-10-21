# ComfyUI Workflow Runner

**ゲーム開発で使っていた画像生成スクリプトから、ワークフロー実行部分を切り出した Python CLI です。**

ComfyUI の API 形式 JSON を送り、実行完了を待ち、画像と保存先一覧をローカルに回収します。キャラクター名、ゲームのディレクトリ構造、特定のモデルに依存しません。CLI 本体とテストは Python 標準ライブラリのみで動きます。

元のツールは、スプライト・肖像画・背景の候補を繰り返し生成するために育ったものです。本リポジトリでは、その共通処理を AI 支援で整理し、固定パスやプロンプト辞書を取り除きました。実測していない性能・品質・導入効果の数値は掲載していません。具体的な由来と除外範囲は [切り出し対応表](docs/EXTRACTION.md) を参照してください。

## できること

- API 形式 JSON の構造確認。接続せずに実行できます。
- 任意の既存ノード入力を JSON 値で上書きして送信。
- 完了・失敗・中断の検出、待機期限と HTTP タイムアウトの指定。
- 有限回の逐次実行、停止ファイル、既存 prompt ID からの回収。
- `/view` 経由の複数画像回収。ComfyUI の出力フォルダー共有は不要です。
- 既定で上書き禁止。ローカル出力先のパストラバーサルと既存シンボリックリンクを拒否。

## セットアップ

Python **3.10 以上**が必要です。パッケージのインストールは不要です。ターミナルでこのリポジトリのルートへ移動してください。

```powershell
python --version
python -m comfy_runner --help
python -m comfy_runner validate examples/solid-color.api.json
python -m unittest discover -s tests -v
```

`validate` とテストは ComfyUI・GPU・モデルを必要とせず、生成やダウンロードも行いません。

実行・回収には、別途セットアップし起動済みの ComfyUI サーバーが必要です。本ツールは ComfyUI、モデル、カスタムノードをインストールしません。サーバーの導入方法は [ComfyUI 本体](https://github.com/Comfy-Org/ComfyUI) を参照してください。

## 最初の実行

付属 JSON は `EmptyImage` と `SaveImage` を使う単色画像の API ワークフローです。チェックポイントもプロンプトも使わず、特定の生成モデルへの対応を主張するサンプルではありません。これらのノードが利用可能な ComfyUI を起動後、実行します。

```powershell
python -m comfy_runner run examples/solid-color.api.json --output-dir outputs/sample
```

`prompt_id=...` が先に表示され、成功時には回収先の JSON 一覧が表示されます。保存名は `0001_0001_0001_<元ファイル名>`、一覧は `0001_manifest.json` です。先頭から実行番号・出力ノードの列挙順・画像番号を表します。ノード番号そのものではありません。

同じ出力先を再利用すると衝突時にエラーになります。別ディレクトリを使うか、意図的に置き換える場合に限り `--overwrite` を指定してください。

## 自分のワークフローを使う

ComfyUI から **API 形式**で書き出した JSON を指定します。UI レイアウト JSON や `{ "prompt": ... }` のリクエスト全体ではなく、トップレベルがノード ID のオブジェクトである必要があります。利用するモデル・ノード・入力画像はサーバー側で準備してください。

```powershell
python -m comfy_runner validate my-workflow.api.json
python -m comfy_runner run my-workflow.api.json --output-dir outputs/custom --api-url http://127.0.0.1:8188 --request-timeout 30 --timeout 1200 --poll-interval 2
```

既存入力だけを `--set NODE INPUT JSON` で変更できます。値は JSON として解釈され、文字列には JSON の引用符が必要です。シェルによる引用符の違いを避けたい場合は、JSON ファイルを直接編集してください。

```powershell
python -m comfy_runner run examples/solid-color.api.json --output-dir outputs/wide --set 1 width 512 --set 1 height 256
```

シードを持つワークフローでは、該当ノードの `seed` 等を同様に指定できます。ノード ID や入力名はワークフローごとに異なります。自動的なシード変更・モデル切り替え・プロンプト補完は行いません。

## 連続実行と回収

```powershell
python -m comfy_runner run examples/solid-color.api.json --output-dir outputs/batch --count 3 --interval 1 --stop-file STOP
python -m comfy_runner fetch YOUR_PROMPT_ID --output-dir outputs/recovered --timeout 1200
python -m comfy_runner run --help
python -m comfy_runner fetch --help
python -m comfy_runner validate --help
```

`--count` の既定値は 1 です。毎回同じワークフローを送信するため、固定シードなら同じ結果になる可能性があります。失敗した時点で停止し、自動再送しません。送信の応答が途切れた場合、サーバー側では受理済みの可能性があります。重複送信を避けるため、サーバーの履歴を先に確認してください。

停止ファイルは送信前と履歴ポーリング時に確認します。`STOP` を作成するか Ctrl+C でローカル処理を停止できますが、**送信済みジョブはキャンセルしません**。停止ファイルは次の実行前に利用者が削除してください。`fetch` も完了まで待つので、期限切れ後の回収に使えます。

## 安全性と制限

- 既定の接続先は `http://127.0.0.1:8188`。他ホストへの接続は `--api-url` で明示します。URL 内の資格情報と HTTP リダイレクトは拒否します。認証・独自ヘッダー・WebSocket は非対応です。接続先と OS のプロキシ設定は信頼できるものを使ってください。
- ワークフローはサーバーで実行されるコードに相当します。信頼できる JSON とカスタムノードのみを使ってください。本ツールはサーバー側のファイル書き込みを隔離せず、`--overwrite` は**回収先だけ**に適用されます。
- 画像は history の `outputs.*.images` を回収します。動画・音声・独自の出力形式・直接ゲームフォルダーに保存するノードは対象外です。画像のない完了はエラーです。
- 完了判定には history の `status.completed: true` を必要とします。古い API や独自実装に対する互換性保証はありません。`validate` は構造のみの確認で、リンク・入力型・ノード登録・モデル存在はサーバーが検証します。
- `--timeout` はジョブごとの履歴待機期限で、送信・画像回収は含みません。HTTP タイムアウトはソケット待機の制限で、全通信の厳密な実時間上限ではありません。ネットワークエラーは即終了します。
- JSON 応答は 16 MiB、画像は 1 件あたり 256 MiB までメモリーに読み込みます。画像バイト列の形式検証・再エンコードはしません。
- 出力はフラットなファイル名に変換し、サーバーの `subfolder` をローカルパスとして使いません。Windows/POSIX の危険なファイル名、予約名、パス区切りを拒否します。出力先を同時に他プロセスが改変する環境の防御は保証しません。
- 回収全体はトランザクションではありません。途中で失敗すると回収済み画像が残り、一覧ファイルは完成しない場合があります。新しい出力先で `fetch` して復旧できます。
- 一覧 JSON は画像の保存先とサーバー側メタデータのみです。元 JSON やモデル情報の完全な再現マニフェストではありません。利用者自身でワークフローと設定を保管してください。

## 検証状況と権利

標準ライブラリのテストで HTTP をモックし、送信・待機・失敗・出力保護・CLI を検証します。実 ComfyUI による生成、GPU 上の互換性確認、モデルのダウンロードはこの切り出し作業では行っていません。

ゲーム素材、プロンプトカタログ、参照画像、モデル重み、元のモデル依存ワークフローは同梱していません。本リポジトリのライセンスは未指定です。利用するモデルや生成物には、それぞれの利用条件が適用されます。

## 開発用 Skill

[comfyui-workflow-runner](.agents/skills/comfyui-workflow-runner/SKILL.md) を同梱しています。API 形式の確認、有限回の実行、prompt ID を使った回収、検証結果の区別をまとめた AI 開発支援用の手順です。このリポジトリを作業フォルダーとして開いて参照できます。
