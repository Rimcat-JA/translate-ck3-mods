# translate-ck3-mods

[English](README.md) | **日本語**

Crusader Kings III（CK3）のMOD保管フォルダを走査し、各MODで実際に使われている言語を自動検出して、選択した翻訳先言語の完全コピーを作るWindowsアプリ兼Codex／Claude Code互換スキルです。

既定ではLM StudioなどのローカルLLMを利用します。高品質な翻訳が必要な場合はOpenAI、OpenRouter、NanoGPT（従量課金／サブスクリプション）もGUIから明示的に選択できます。翻訳結果はSQLiteへ逐次保存され、長いMODでも中断・再開できます。

## 主な機能

- 複数のCK3 MODを一括翻訳
- 英語表記のWindows GUIで、翻訳元言語と翻訳先言語をそれぞれ指定
- CK3のMODフォルダ全体または別の保管フォルダを走査し、チェックリストから個別MODを選択
- 個別のMODフォルダや外部`.mod`ランチャーdescriptorも追加可能
- descriptorのパス解決とCK3フォルダ構造検証を行い、不正な候補を選択不可にする
- 本文から英語・日本語・ロシア語・中国語・韓国語・複数の欧州言語を自動検出
- 文章を持たないスクリプト専用MODを「Non-linguistic」と表示し、翻訳先と同じ言語のMODも選択不可にする
- 元MOD全体をCK3ローカルMODフォルダへ複製し、選択した翻訳元ローカライズだけを検証済みの翻訳先へ置換
- ローカルLLM／OpenAI／OpenRouter／NanoGPTを画面で選択
- LM Studioのnative v1／OpenAI互換／旧v0モデル一覧APIからモデルを検出し、一覧が空でもモデルIDを手動指定可能
- 翻訳結果を単一の独立CK3 MOD、ランチャー用descriptor、manifest、再現可能なZIPへ自動統合
- 日本語を含む任意の翻訳先言語と`l_<locale>`ヘッダーに対応
- `$VALUE$`、`[Character.GetName]`、`#EMP`、`#!`、`@icon!`などのCK3構文を保護
- 長文の安全な分割と失敗項目のみの再試行
- SQLite翻訳メモリによる中断・再開
- 成人向け／NSFW文章を省略・検閲しない翻訳指示
- UTF-8 BOM、ファイル数、キー、トークン、物理改行、文字化けの検証
- 既存翻訳を`localization`外へ退避してから安全に導入
- APIキーとローカルサーバー用トークンをWindows資格情報マネージャーへ暗号化保存し、設定JSON・ログ・manifestには保存しない設計
- リモートAPIキーを公式HTTPSエンドポイント以外へ送信できない許可リスト
- LLMには翻訳本文だけを担当させ、コピー・検証・descriptor・バックアップ等は通常のPythonで処理

## EXEですぐ使う

[GitHub Releases](https://github.com/Rimcat-JA/translate-ck3-mods/releases/latest)から配布ZIPをダウンロードし、`CK3_Mod_Translator.exe`を起動します。Pythonやコマンド操作は不要です。

1. ローカルLLMを使う場合は、LM StudioのLocal Serverを開始します。先にモデルを読み込むか、LM StudioのJust-in-Time（JIT）読み込みを有効にします。
2. 「Advanced Settings」を開いて「Refresh Models」を押します。一覧から選ぶか、一覧が空ならモデルIDを直接入力します。
3. 翻訳元は「Auto-detect」、翻訳先は希望する言語を選びます。
4. CK3のMODフォルダ、別の保管フォルダ、個別MODフォルダ、または`.mod`ファイルを読み込みます。
5. 検出言語と状態を確認して対象にチェックを付け、「Translate Selected Mods」を押します。

生成物は既定で次へ配置され、CK3ランチャーからすぐ選択できます。

```text
Documents\Paradox Interactive\Crusader Kings III\mod\<元MOD名>_<翻訳先言語>
Documents\Paradox Interactive\Crusader Kings III\mod\<元MOD名>_<翻訳先言語>.mod
```

元MODは変更されません。格納先の`l_english`などと実際の本文言語を分けて判定するため、英語用ファイルに日本語本文が入っているMODも「Japanese」と表示され、日本語への再翻訳はできません。生成版では選択した翻訳元・翻訳先ローカライズだけを置換し、スクリプト、衣装、画像、音声などはハッシュ比較により完全コピーを確認します。ランチャーでは生成版だけを有効にし、同じ元MODを同時に有効化しないでください。

### API方式と保存場所

- ローカルLLM：翻訳元本文をPC外へ送信せず、API料金も発生しません。
- OpenAI／OpenRouter／NanoGPT：翻訳元本文を選択サービスの公式APIへ送信し、料金が発生する場合があります。開始前に確認画面を表示します。
- APIキー／ローカルトークン：保存を有効にした場合、Windows資格情報マネージャーへ暗号化保存します。ローカルトークンは、LM Studioの「Require Authentication」を有効にした場合だけ必要です。
- 設定・ログ・キャッシュ：`%LOCALAPPDATA%\CK3JapaneseModMaker`だけに保存し、自動アップロードしません。v1の設定と翻訳キャッシュを再利用できるよう、保存先名は互換性のため維持しています。
- ログにはAPIキー／トークンと翻訳本文を記録しません。

## 必要なもの

- Windows 10／11（配布EXEを使う場合）
- `descriptor.mod`を持つ展開済みCK3 MOD、またはそこへ解決できる外部`.mod`ファイル
- 翻訳するMODには認識可能なロケールの自然言語YML
- ローカルLLMサーバー、または選択したAPIサービスのキー

ソースから実行・ビルドする場合だけPython 3.10以降が必要です。

既定の接続先はLM Studioの`http://127.0.0.1:1234/v1/chat/completions`です。モデル本体はこのリポジトリに含まれません。

「Refresh Models」は、この接続先からサーバーを判定し、LM Studioの`/api/v1/models`、OpenAI互換の`/v1/models`、旧版の`/api/v0/models`を利用できます。JIT読み込みを使う場合やモデル一覧を返さない互換サーバーでも利用できるよう、モデル欄にはモデルIDを直接入力できます。

## 推奨ローカルLLM

成人向け表現を含む大規模なCK3 MODの翻訳には、今回の実運用で使用した次のモデルを推奨します。

```text
qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive
```

テストしたGGUFファイルは次の量子化版です。

```text
Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf
```

このモデルはLM Studio上で、成人向け／NSFW文章を拒否・省略しにくく、長いイベント文章も自然な日本語へ翻訳できました。実際にCarnalitas、CBO Unofficial、Phaze Futanariの合計9,389項目に含まれる長文や複雑なCK3トークンの翻訳にも使用しています。

今回の動作設定は、コンテキスト長32,768、並列数4、フルGPUオフロードです。LM Studio上の読み込みサイズは約22GBだったため、利用環境に応じてコンテキスト長、並列数、GPUオフロードを下げてください。モデルは本リポジトリには同梱されません。

## スキルの導入

Codexでは、`SKILL.md`、`agents`、`scripts`、`references`を次へ配置します。

```text
~/.codex/skills/translate-ck3-mods/
```

Claude Codeでも同じ構成を次へ配置します（`agents`は省略可能です）。

```text
~/.claude/skills/translate-ck3-mods/
```

Windows PowerShellでの例：

```powershell
git clone https://github.com/Rimcat-JA/translate-ck3-mods.git "$HOME\translate-ck3-mods"
$source = "$HOME\translate-ck3-mods"
$target = "$HOME\.codex\skills\translate-ck3-mods" # Claude Code: $HOME\.claude\skills\translate-ck3-mods
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item "$source\SKILL.md" -Destination $target -Force
Copy-Item "$source\agents","$source\scripts","$source\references" -Destination $target -Recurse -Force
```

導入後、エージェントを再起動し、次のように呼び出します。

```text
$translate-ck3-mods を使い、このCK3 MODをLM Studioのモデルで日本語化して導入してください。
```

## ソースからEXEをビルド

PyInstallerとPillowを導入したWindows環境で次を実行します。

```powershell
.\build_exe.ps1
```

`dist/CK3_Mod_Translator.exe`、SHA256ファイル、英語・日本語説明書入り配布ZIPが生成されます。

## CLIの使用例

### MOD保管フォルダをモデルに接続せず検査

```powershell
python scripts/ck3_mod_scanner.py "C:\path\to\Crusader Kings III\mod"
```

### 1つのMODを完全コピーして翻訳

```powershell
python scripts/ck3_clone.py "C:\path\to\Example Mod"
```

ローカルモデルは自動検出され、出力先もCK3ローカルMODフォルダへ自動設定されます。

### 翻訳から単一MOD作成まで一括実行

[references/pipeline.example.json](references/pipeline.example.json)をコピーして内容を編集し、次を実行します。

```powershell
python scripts/ck3_pipeline.py --config "C:\work\ck3-pipeline.json"
```

設定された全MODをローカルLLMで翻訳し、検証後、次を自動生成します。

```text
<destination>/<bundle-id>/              単一の独立CK3 MOD
<destination>/<bundle-id>.mod           ランチャー用descriptor
<destination>/<bundle-id>.zip           再現可能な配布用ZIP
```

manifestには、PC固有の元パスを含めず、入力ファイルのハッシュ、項目数、競合、依存MOD、出力ハッシュを記録します。異なる訳文を持つ重複キーが見つかった場合は、既定で停止します。既存成果物も黙って削除せず、`"overwrite": true`の場合は先に`_bundle_backups`へ退避します。

この複数MOD用設定パイプラインは安全なローカルLLM専用です。GUI／`ck3_clone.py`では、ユーザーが明示的に選択した場合だけ公式リモートAPIを利用できます。どの方式でもLLMを呼ぶのは翻訳本文の生成だけです。

### 翻訳

翻訳は必ずゲームのMODフォルダとは別の作業先へ生成してください。

```powershell
python scripts/ck3_localize.py translate `
  --mod "Example Mod=C:\path\to\Example Mod" `
  --output "C:\work\ck3-ja" `
  --cache "C:\work\ck3-ja.sqlite" `
  --source-language English `
  --source-locale l_english `
  --language Japanese `
  --locale l_japanese `
  --endpoint http://127.0.0.1:1234/v1/chat/completions `
  --model your-local-model-id `
  --workers 4
```

複数MODを処理する場合は`--mod`を繰り返します。同じ`--cache`を使って再実行すると、検証済みの既訳を再利用します。

翻訳語を統一したい場合は、UTF-8のJSON辞書を指定できます。

```json
{
  "trait": "特性",
  "liege": "主君",
  "vassal": "封臣"
}
```

```powershell
python scripts/ck3_localize.py translate ... --glossary "C:\work\glossary.json"
```

### 検証

モデルへ接続せず、生成済みファイルを再検証できます。

```powershell
python scripts/ck3_localize.py validate `
  --mod "Example Mod=C:\path\to\Example Mod" `
  --output "C:\work\ck3-ja" `
  --source-language English `
  --source-locale l_english `
  --language Japanese `
  --locale l_japanese
```

### 導入

検証に合格してから実行してください。

```powershell
python scripts/ck3_localize.py install `
  --mapping "C:\work\ck3-ja\Example Mod=C:\Users\me\Documents\Paradox Interactive\Crusader Kings III\mod\Example Mod" `
  --locale l_japanese
```

既存の翻訳は次のように、CK3の読み込み対象外へ移動されます。

```text
<mod>/_translation_backups/japanese_<timestamp>/
```

## 問題が起きた場合

- 「Refresh Models」でローカルモデルが表示されない場合は、LM StudioのDeveloper画面でサーバーが起動中か、接続先のホストとポートが一致しているかを確認します。次にチャット用モデルをインポート／ダウンロードし、手動で読み込むかJIT読み込みを有効にして再取得してください。それでも空なら、LM Studioに表示される正確なモデルIDをモデル欄へ直接貼り付けます。
- LM Studioの「Require Authentication」を有効にしている場合は、Advanced SettingsでAPIトークンを入力します。保存は任意で、Windows資格情報マネージャーだけを使用し、設定ファイルやログには書き込みません。
- トークン保持に失敗する場合は`--batch-items`と`--long-segment`を小さくします。
- ローカルモデルの同時処理数に合わせて`--workers`を調整します。
- モデルがJSON Schemaを受け付けない場合、スクリプトは通常のJSON出力へ自動的にフォールバックします。
- 未完項目が残っても成功済み翻訳はSQLiteに保存されています。同じコマンドを再実行してください。
- 自動判定の信頼度が低いMODは自動選択されません。内容を確認して翻訳元言語を明示してください。
- バックアップを`localization`配下へ置かないでください。CK3が旧訳も読み込む可能性があります。

詳しい手順は[SKILL.md](SKILL.md)、CK3固有の検証規則は[references/ck3-localization.md](references/ck3-localization.md)を参照してください。

## Repository layout

```text
README.md                       English documentation
README.ja.md                    日本語ドキュメント
SKILL.md                         エージェント用ワークフロー
agents/openai.yaml              Codex用UIメタデータ
scripts/ck3_localize.py         翻訳・検証・導入CLI
scripts/ck3_clone.py            完全コピー型の単一MOD翻訳エンジン
scripts/ck3_gui.py              Windows GUIエントリーポイント
scripts/ck3_mod_scanner.py      descriptor・構造・実言語スキャナー
scripts/ck3_languages.py        CK3ロケール定義・パス変換
scripts/ck3_providers.py        APIプロバイダー許可リスト
scripts/windows_credentials.py  Windows資格情報マネージャー連携
scripts/ck3_pipeline.py         ローカル翻訳からパッケージ化までの一括処理
scripts/ck3_bundle.py           LLMを使わない決定論的な単一MODビルダー
build_exe.ps1                   単体Windows EXE・配布ZIPビルド
packaging/                      バージョン情報・配布説明書
tests/                          ローカル／全API／凍結EXEのE2Eテスト
references/pipeline.example.json 一括処理の設定例
references/ck3-localization.md  CK3ローカライズ仕様
references/agent-compatibility.md Codex／Claude Code導入情報
```
