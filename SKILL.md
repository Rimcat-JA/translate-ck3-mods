---
name: translate-ck3-mods
description: Translate Crusader Kings III mod localization with a local LLM or an explicitly selected OpenAI, OpenRouter, or NanoGPT API. Use when Codex or Claude must preserve CK3 tokens, resume translations, create a complete Japanese clone through the Windows GUI/EXE, validate YAML, combine translations, safely install files, or repair machine translation.
---

# Translate CK3 Mods

Create complete, resumable CK3 mod localizations. Default to a local model; use OpenAI, OpenRouter, or NanoGPT only when the user explicitly selects that provider and understands that text leaves the PC and charges may apply. Every LLM may translate localization values only. Deterministic Python must perform discovery, token protection, copying, validation, conflict handling, packaging, and installation. Treat explicit adult text like any other source text and translate it faithfully.

## Workflow

1. Identify every mod root and its `localization/english` tree. Work from an extracted copy when the source is a ZIP.
2. Confirm the provider, target language, CK3 locale, endpoint, and model. Remote keys may be stored only in Windows Credential Manager; never place them in scripts, settings JSON, SQLite, logs, manifests, or generated mods.
3. Read [references/ck3-localization.md](references/ck3-localization.md) before changing localization files. Read [references/providers.md](references/providers.md) before configuring a remote API. Read [references/agent-compatibility.md](references/agent-compatibility.md) when packaging or installing the skill for another agent.
4. Stage translations outside the live mod directory. Reuse the same SQLite cache on every run so completed entries survive interruptions and retries.
5. For ordinary Windows users, prefer `dist/CK3_Japanese_Mod_Maker.exe` when the packaged app is available; otherwise run `python scripts/ck3_gui.py`. Select the source folder and create the clone. It installs the clone and launcher descriptor into the CK3 local-mod directory automatically.
6. For automation, run `scripts/ck3_clone.py <mod-root>`. Prefer 4 workers locally and conservative parallelism for paid APIs.
7. Do not install or publish unless file count, keys, CK3 tokens, headers, UTF-8 BOM, placeholders, single-line values, and non-localization copy hashes all pass.
8. Review terminology and a sample from UI labels, game rules, decisions, events, and explicit prose. Supply a JSON glossary and rerun when recurring terms are inconsistent.
9. When the user wants one localization-only bundle from multiple mods, run `scripts/ck3_pipeline.py` with a reviewed local-only JSON configuration.
10. Keep `collision_policy` set to `error` unless the user has reviewed a conflicting duplicate key and explicitly chosen `first` or `last`.
11. Run the `install` subcommand only after validation when installing translations into each source mod instead of creating a complete clone.
12. Validate the generated or installed tree again and report counts, cache path, backup path, provider, model, and any entries still requiring review.

## Commands

Launch the end-user GUI or create a complete clone from one path:

```powershell
dist\CK3_Japanese_Mod_Maker.exe
python scripts\ck3_gui.py
python scripts/ck3_clone.py "C:\path\to\Mod"
```

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

Translate and package multiple sources as one mod with one command:

```powershell
Copy-Item references/pipeline.example.json C:\work\ck3-pipeline.json
# Edit paths, local endpoint, model, bundle id, and bundle name first.
python scripts/ck3_pipeline.py --config C:\work\ck3-pipeline.json
```

The pipeline accepts only loopback endpoints (`localhost`, `127.x.x.x`, or `::1`). `scripts/ck3_bundle.py` has no network or LLM code.

## Guardrails

- Preserve localization keys and every CK3 command token exactly. Never translate `$...$`, `[...]`, `#style`, `#!`, `@icon!`, escaped newlines, or scripted braces.
- Keep generated YAML UTF-8 with BOM and use the requested `l_<locale>:` header.
- Do not put backups beneath `localization`; CK3 can recursively load them.
- Never overwrite source English files or the live locale directory before staged validation.
- Default to loopback local mode. Never switch to a paid or hosted provider without explicit user selection.
- Send remote API keys only to the exact HTTPS endpoints allowlisted in `scripts/ck3_providers.py`.
- Use any selected LLM only to translate localization values; never ask it to discover files, copy a mod, resolve key collisions, write descriptors, validate, or package.
- Never log an API key or translation source text. Settings and operational logs stay local; credentials use Windows Credential Manager.
- Default to an error for conflicting duplicate localization keys. Never let a weak model choose load order or discard entries.
- Do not equate visual-age mods with immortality when handling adjacent CK3 mod setup.
- Do not mark the task complete while the cache has failed or pending entries.
- Treat a model refusal, English copy, Simplified Chinese leakage in Japanese output, JSON residue, runaway vocalization, or actual newline inside a value as a failed translation.
