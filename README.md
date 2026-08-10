# translate-ck3-mods

**English** | [日本語](README.ja.md)

A Codex and Claude Code compatible skill for translating Crusader Kings III mods into a user-selected language with a local LLM.

It works with OpenAI-compatible endpoints exposed by LM Studio, Ollama, llama.cpp, vLLM, and similar runtimes. Completed translations are stored incrementally in SQLite, so large projects can be interrupted, resumed, and reused without retranslating finished entries.

## Features

- Translate one or more CK3 mods in a single run
- Support Japanese and other user-selected target languages and `l_<locale>` headers
- Protect CK3 syntax such as `$VALUE$`, `[Character.GetName]`, `#EMP`, `#!`, and `@icon!`
- Split long entries safely and retry only failed records
- Resume from a SQLite translation-memory cache
- Instruct the model to translate adult/NSFW text faithfully without censorship or omission
- Validate UTF-8 BOM, file counts, keys, tokens, physical newlines, placeholders, and encoding errors
- Back up an existing localization outside `localization` before installation
- Keep API keys, model files, and machine-specific paths out of the repository

## Requirements

- Python 3.10 or later
- A local LLM server exposing an OpenAI-compatible Chat Completions API
- An extracted CK3 mod folder containing `localization/english`

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

In LM Studio this model translated explicit/NSFW event prose without frequent refusals or omissions. It was also used as the final fallback for long entries and difficult CK3 token structures while translating 9,389 entries from Carnalitas, CBO Unofficial, and Phaze Futanari.

The tested setup used a 32,768-token context, four parallel slots, and full GPU offload. LM Studio reported an approximately 22 GB loaded size. Reduce context length, parallelism, or GPU offload when using hardware with less memory. The model itself is not distributed by this repository.

## Install as a skill

For Codex, copy the repository to:

```text
~/.codex/skills/translate-ck3-mods/
```

For Claude Code, copy it to:

```text
~/.claude/skills/translate-ck3-mods/
```

Windows PowerShell example:

```powershell
git clone https://github.com/Rimcat-JA/translate-ck3-mods.git "$HOME\translate-ck3-mods"
Copy-Item "$HOME\translate-ck3-mods" "$HOME\.codex\skills\translate-ck3-mods" -Recurse
# For Claude Code:
Copy-Item "$HOME\translate-ck3-mods" "$HOME\.claude\skills\translate-ck3-mods" -Recurse
```

Restart the agent after installation, then invoke the skill with a request such as:

```text
Use $translate-ck3-mods to translate this CK3 mod into Japanese with my local LM Studio model and install it.
```

## CLI usage

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
references/ck3-localization.md    CK3 localization rules
references/agent-compatibility.md Codex and Claude Code installation notes
```
