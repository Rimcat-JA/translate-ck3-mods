# translate-ck3-mods

**English** | [日本語](README.ja.md)

A Windows desktop application and Codex/Claude Code compatible skill that creates a complete Japanese copy of a Crusader Kings III mod by selecting one folder.

Local LLM mode is the default. Users may explicitly select OpenAI, OpenRouter, or NanoGPT for higher-quality remote translation. Completed translations are stored incrementally in SQLite, so large projects can be interrupted and resumed.

## Features

- Translate one or more CK3 mods in a single run
- Native Windows GUI: select a mod folder and press one button
- Copy the complete source mod into CK3's local mod directory and replace English localization with validated Japanese
- Select Local LLM, OpenAI, OpenRouter, NanoGPT pay-as-you-go, or NanoGPT subscription mode
- Build the translated results into one standalone CK3 mod, launcher descriptor, manifest, and deterministic ZIP
- Support Japanese and other user-selected target languages and `l_<locale>` headers
- Protect CK3 syntax such as `$VALUE$`, `[Character.GetName]`, `#EMP`, `#!`, and `@icon!`
- Split long entries safely and retry only failed records
- Resume from a SQLite translation-memory cache
- Instruct the model to translate adult/NSFW text faithfully without censorship or omission
- Validate UTF-8 BOM, file counts, keys, tokens, physical newlines, placeholders, and encoding errors
- Back up an existing localization outside `localization` before installation
- Store optional API keys in Windows Credential Manager rather than settings, logs, manifests, or SQLite
- Allow remote keys only to each provider's exact official HTTPS endpoint
- Use an LLM only for translation values; ordinary Python handles copying, validation, descriptors, manifests, and backups

## Use the Windows app

Download the release ZIP from [GitHub Releases](https://github.com/Rimcat-JA/translate-ck3-mods/releases/latest) and launch `CK3_Japanese_Mod_Maker.exe`; Python and command-line work are not required.

1. For local mode, load a model and start the LM Studio Local Server.
2. Select a translation provider. Local LLM is the default.
3. Select an extracted CK3 mod folder.
4. Press **Create Japanese Mod**.

The app writes the complete translated clone and launcher descriptor to:

```text
Documents\Paradox Interactive\Crusader Kings III\mod\<source>_Japanese
Documents\Paradox Interactive\Crusader Kings III\mod\<source>_Japanese.mod
```

The source is never modified. In the clone, `localization/english` is replaced by validated `localization/japanese`; scripts, assets, clothing, audio, and other files are byte-verified copies. Enable the generated clone instead of enabling it together with the original.

Remote modes send localization text to the selected official provider and may incur charges. API keys can be encrypted in Windows Credential Manager. Settings, logs, and caches stay under `%LOCALAPPDATA%\CK3JapaneseModMaker`; logs never contain API keys or translation text.

## Requirements

- Windows 10/11 for the packaged executable
- An extracted CK3 mod folder containing `localization/english`
- A local LLM server or an API key for the explicitly selected remote provider

Python 3.10 or later is required only for source execution or building.

The default endpoint is LM Studio's `http://127.0.0.1:1234/v1/chat/completions`. No model weights are included in this repository.

## Recommended local LLM

For large CK3 mods containing adult content, the model used successfully in the original production translation is recommended:

```text
qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive
```

The tested GGUF quantization was:

```text
Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf
```

In LM Studio this model translated explicit/NSFW event prose without frequent refusals or omissions. It handled long entries and difficult CK3 token structures while translating 9,389 entries from Carnalitas, CBO Unofficial, and Phaze Futanari.

The tested setup used a 32,768-token context, four parallel slots, and full GPU offload. LM Studio reported an approximately 22 GB loaded size. Reduce context length, parallelism, or GPU offload when using hardware with less memory. The model itself is not distributed by this repository.

## Install as a skill

For Codex, copy `SKILL.md`, `agents`, `scripts`, and `references` to:

```text
~/.codex/skills/translate-ck3-mods/
```

For Claude Code, use the same layout at the following location (`agents` is optional):

```text
~/.claude/skills/translate-ck3-mods/
```

Windows PowerShell example:

```powershell
git clone https://github.com/Rimcat-JA/translate-ck3-mods.git "$HOME\translate-ck3-mods"
$source = "$HOME\translate-ck3-mods"
$target = "$HOME\.codex\skills\translate-ck3-mods" # Claude Code: $HOME\.claude\skills\translate-ck3-mods
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item "$source\SKILL.md" -Destination $target -Force
Copy-Item "$source\agents","$source\scripts","$source\references" -Destination $target -Recurse -Force
```

Restart the agent after installation, then invoke the skill with a request such as:

```text
Use $translate-ck3-mods to translate this CK3 mod into Japanese with my local LM Studio model and install it.
```

## Build the executable

With PyInstaller and Pillow installed on Windows:

```powershell
.\build_exe.ps1
```

This generates the single-file EXE, SHA256 checksum, and a distributable ZIP under `dist/`.

## CLI usage

Create a complete Japanese clone from one path:

```powershell
python scripts/ck3_clone.py "C:\path\to\Example Mod"
```

### One-command translation and standalone bundle

Copy [references/pipeline.example.json](references/pipeline.example.json), edit it, then run:

```powershell
python scripts/ck3_pipeline.py --config "C:\work\ck3-pipeline.json"
```

The command translates every configured source with the local LLM, validates the staged files, and deterministically generates:

```text
<destination>/<bundle-id>/              Standalone CK3 mod
<destination>/<bundle-id>.mod           Launcher descriptor
<destination>/<bundle-id>.zip           Reproducible distribution archive
```

The manifest records source file hashes, entry counts, collisions, dependencies, and output hashes without storing machine-specific source paths. Conflicting duplicate keys stop the build by default. Existing bundles are never silently deleted; `"overwrite": true` moves them to `_bundle_backups` first.

This multi-mod configuration pipeline remains local-only. The GUI and `ck3_clone.py` can use an official remote API only after explicit user selection. In every mode, an LLM is called exclusively for translation values; all other operations are deterministic Python.

### Translate

Always stage output outside the live game mod directory.

```powershell
python scripts/ck3_localize.py translate `
  --mod "Example Mod=C:\path\to\Example Mod" `
  --output "C:\work\ck3-ja" `
  --cache "C:\work\ck3-ja.sqlite" `
  --language Japanese `
  --locale l_japanese `
  --endpoint http://127.0.0.1:1234/v1/chat/completions `
  --model qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive `
  --workers 4
```

Repeat `--mod` for additional mods. Rerun the same command with the same `--cache` path to reuse every validated translation already completed.

To enforce terminology, provide a UTF-8 JSON glossary:

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

### Validate

Validate staged files without contacting the model:

```powershell
python scripts/ck3_localize.py validate `
  --mod "Example Mod=C:\path\to\Example Mod" `
  --output "C:\work\ck3-ja" `
  --language Japanese `
  --locale l_japanese
```

### Install

Run this only after validation passes:

```powershell
python scripts/ck3_localize.py install `
  --mapping "C:\work\ck3-ja\Example Mod=C:\Users\me\Documents\Paradox Interactive\Crusader Kings III\mod\Example Mod" `
  --locale l_japanese
```

Existing localization is moved outside CK3's localization loading tree:

```text
<mod>/_translation_backups/japanese_<timestamp>/
```

## Troubleshooting

- Reduce `--batch-items` and `--long-segment` when the model fails to preserve tokens.
- Match `--workers` to the local server's configured parallel limit.
- When a server rejects JSON Schema, the script automatically falls back to ordinary JSON output.
- Successful entries remain in SQLite even when some entries fail. Rerun the same command after adjusting settings.
- Never place backups beneath `localization`; CK3 may load the old keys as duplicates.

See [SKILL.md](SKILL.md) for the agent workflow and [references/ck3-localization.md](references/ck3-localization.md) for CK3-specific validation rules.

## Repository layout

```text
README.md                         English documentation
README.ja.md                      Japanese documentation
SKILL.md                          Agent workflow
agents/openai.yaml                Codex UI metadata
scripts/ck3_localize.py           Translation, validation, and installation CLI
scripts/ck3_clone.py              Complete-clone single-mod translation engine
scripts/ck3_gui.py                Windows desktop UI entry point
scripts/ck3_providers.py          Provider definitions and endpoint allowlist
scripts/windows_credentials.py    Windows Credential Manager integration
scripts/ck3_pipeline.py           One-command local translation and packaging pipeline
scripts/ck3_bundle.py             Deterministic non-LLM standalone mod builder
build_exe.ps1                     Single-file Windows EXE and release ZIP build
packaging/                        Version metadata and end-user guide
tests/                            Local, remote-provider, and frozen-EXE tests
references/pipeline.example.json  Pipeline configuration example
references/ck3-localization.md    CK3 localization rules
references/agent-compatibility.md Codex and Claude Code installation notes
```
