---
name: translate-ck3-mods
description: Translate Crusader Kings III mod localization into a user-selected language with a local OpenAI-compatible LLM such as LM Studio, Ollama, llama.cpp, or vLLM. Use when Codex or Claude must discover CK3 localization files, preserve game tokens and formatting, resume large or NSFW translations with SQLite translation memory, validate generated YAML, safely install translated files, or repair an existing machine translation.
---

# Translate CK3 Mods

Create complete, resumable CK3 mod localizations without sending content to a hosted service unless the user explicitly requests one. Treat explicit adult text like any other source text and translate it faithfully.

## Workflow

1. Identify every mod root and its `localization/english` tree. Work from an extracted copy when the source is a ZIP.
2. Confirm the target language, CK3 locale header, local endpoint, and loaded model. Query `/v1/models` or use the local runtime CLI when discoverable. Never save API keys in this skill, scripts, databases, or generated mods.
3. Read [references/ck3-localization.md](references/ck3-localization.md) before changing localization files. Read [references/agent-compatibility.md](references/agent-compatibility.md) when packaging or installing the skill for another agent.
4. Stage translations outside the live mod directory. Reuse the same SQLite cache on every run so completed entries survive interruptions and retries.
5. Run `scripts/ck3_localize.py translate`. Prefer 4 workers for a local model unless the server advertises a different parallel limit. Use small batches for weak models and reduce `--long-segment` when token preservation fails.
6. Run the `validate` subcommand. Do not install unless file count, keys, CK3 tokens, headers, UTF-8 BOM, placeholders, and single-line YAML values all pass.
7. Review terminology and a sample from UI labels, game rules, decisions, events, and explicit prose. Supply a JSON glossary and rerun when recurring terms are inconsistent.
8. Run the `install` subcommand only after validation. It moves the old locale directory to `_translation_backups` outside `localization`, preventing duplicate keys from loading.
9. Validate the installed tree again and report counts, cache path, backup path, model, and any entries still requiring review.

## Commands

Translate one or more mods:

```powershell
python scripts/ck3_localize.py translate `
  --mod "Carnalitas=C:\path\to\Carnalitas" `
  --mod "Another Mod=C:\path\to\Another Mod" `
  --output "C:\work\ck3-ja" `
  --cache "C:\work\ck3-ja.sqlite" `
  --language Japanese --locale l_japanese `
  --endpoint http://127.0.0.1:1234/v1/chat/completions `
  --model local-model-id --workers 4
```

Validate without contacting the model:

```powershell
python scripts/ck3_localize.py validate --mod "Carnalitas=C:\path\to\Carnalitas" --output "C:\work\ck3-ja" --language Japanese --locale l_japanese
```

Install after validation:

```powershell
python scripts/ck3_localize.py install --mapping "C:\work\ck3-ja\Carnalitas=C:\Users\me\Documents\Paradox Interactive\Crusader Kings III\mod\Carnalitas" --locale l_japanese
```

Use `python scripts/ck3_localize.py <subcommand> --help` for all tuning options.

## Guardrails

- Preserve localization keys and every CK3 command token exactly. Never translate `$...$`, `[...]`, `#style`, `#!`, `@icon!`, escaped newlines, or scripted braces.
- Keep generated YAML UTF-8 with BOM and use the requested `l_<locale>:` header.
- Do not put backups beneath `localization`; CK3 can recursively load them.
- Never overwrite source English files or the live locale directory before staged validation.
- Do not equate visual-age mods with immortality when handling adjacent CK3 mod setup.
- Do not mark the task complete while the cache has failed or pending entries.
- Treat a model refusal, English copy, Simplified Chinese leakage in Japanese output, JSON residue, runaway vocalization, or actual newline inside a value as a failed translation.
