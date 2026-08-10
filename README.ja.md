# translate-ck3-mods

[English](README.md) | **日本語**

ローカルLLMを使ってCrusader Kings III（CK3）のMODを任意の言語へ翻訳する、Codex／Claude Code互換スキルです。

LM Studio、Ollama、llama.cpp、vLLMなどのOpenAI互換APIを利用し、翻訳結果をSQLiteへ逐次保存します。長いMODでも中断・再開でき、既に翻訳した文章を再利用できます。

## 主な機能

- 複数のCK3 MODを一括翻訳
- 日本語を含む任意の翻訳先言語と`l_<locale>`ヘッダーに対応
- `$VALUE$`、`[Character.GetName]`、`#EMP`、`#!`、`@icon!`などのCK3構文を保護
- 長文の安全な分割と失敗項目のみの再試行
- SQLite翻訳メモリによる中断・再開
- 成人向け／NSFW文章を省略・検閲しない翻訳指示
- UTF-8 BOM、ファイル数、キー、トークン、物理改行、文字化けの検証
- 既存翻訳を`localization`外へ退避してから安全に導入
- APIキー、モデル、PC固有パスをリポジトリへ保存しない設計

## 必要なもの

- Python 3.10以降
- OpenAI互換Chat Completions APIを提供するローカルLLMサーバー
- 翻訳対象MODの展開済みフォルダ

既定の接続先はLM Studioの`http://127.0.0.1:1234/v1/chat/completions`です。モデル本体はこのリポジトリに含まれません。

## 推奨ローカルLLM

成人向け表現を含む大規模なCK3 MODの翻訳には、今回の実運用で使用した次のモデルを推奨します。

```text
qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive
```

テストしたGGUFファイルは次の量子化版です。

```text
Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf
```

このモデルはLM Studio上で、成人向け／NSFW文章を拒否・省略しにくく、長いイベント文章も自然な日本語へ翻訳できました。実際にCarnalitas、CBO Unofficial、Phaze Futanariの合計9,389項目を翻訳する際、外部APIで失敗した長文や複雑なCK3トークンを含む文章の最終処理にも使用しています。

今回の動作設定は、コンテキスト長32,768、並列数4、フルGPUオフロードです。LM Studio上の読み込みサイズは約22GBだったため、利用環境に応じてコンテキスト長、並列数、GPUオフロードを下げてください。モデルは本リポジトリには同梱されません。

## スキルの導入

Codexでは、このリポジトリ全体を次へ配置します。

```text
~/.codex/skills/translate-ck3-mods/
```

Claude Codeでは次へ配置します。

```text
~/.claude/skills/translate-ck3-mods/
```

Windows PowerShellでの例：

```powershell
git clone https://github.com/Rimcat-JA/translate-ck3-mods.git "$HOME\translate-ck3-mods"
Copy-Item "$HOME\translate-ck3-mods" "$HOME\.codex\skills\translate-ck3-mods" -Recurse
# Claude Codeの場合：
Copy-Item "$HOME\translate-ck3-mods" "$HOME\.claude\skills\translate-ck3-mods" -Recurse
```

導入後、エージェントを再起動し、次のように呼び出します。

```text
$translate-ck3-mods を使い、このCK3 MODをLM Studioのモデルで日本語化して導入してください。
```

## CLIの使用例

### 翻訳

翻訳は必ずゲームのMODフォルダとは別の作業先へ生成してください。

```powershell
python scripts/ck3_localize.py translate `
  --mod "Example Mod=C:\path\to\Example Mod" `
  --output "C:\work\ck3-ja" `
  --cache "C:\work\ck3-ja.sqlite" `
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

- トークン保持に失敗する場合は`--batch-items`と`--long-segment`を小さくします。
- ローカルモデルの同時処理数に合わせて`--workers`を調整します。
- モデルがJSON Schemaを受け付けない場合、スクリプトは通常のJSON出力へ自動的にフォールバックします。
- 未完項目が残っても成功済み翻訳はSQLiteに保存されています。同じコマンドを再実行してください。
- バックアップを`localization`配下へ置かないでください。CK3が旧訳も読み込む可能性があります。

詳しい手順は[SKILL.md](SKILL.md)、CK3固有の検証規則は[references/ck3-localization.md](references/ck3-localization.md)を参照してください。

## Repository layout

```text
README.md                       English documentation
README.ja.md                    日本語ドキュメント
SKILL.md                         エージェント用ワークフロー
agents/openai.yaml              Codex用UIメタデータ
scripts/ck3_localize.py         翻訳・検証・導入CLI
references/ck3-localization.md  CK3ローカライズ仕様
references/agent-compatibility.md Codex／Claude Code導入情報
```
