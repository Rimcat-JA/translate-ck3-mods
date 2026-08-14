---
name: translate-ck3-mods
description: Detect and translate Crusader Kings III mod localization with a local LLM or an explicitly selected OpenAI, OpenRouter, or NanoGPT API. Use when Codex or Claude must scan CK3 mod libraries, validate descriptor and folder structure, identify the actual source language, avoid retranslating target-language mods, preserve CK3 tokens, resume translations, create complete translated clones through the Windows GUI/EXE, validate YAML, combine translations, safely install files, or repair machine translation.
---

# Translate CK3 Mods

Create complete, resumable CK3 mod localizations. Default to a local model; use OpenAI, OpenRouter, or NanoGPT only when the user explicitly selects that provider and understands that text leaves the PC and charges may apply. Every LLM may translate localization values only. Deterministic Python must perform discovery, token protection, copying, validation, conflict handling, packaging, and installation. Treat explicit adult text like any other source text and translate it faithfully.

## Workflow

1. Resolve mod roots from external `.mod` launcher descriptors or internal `descriptor.mod` files. Validate the CK3 folder structure, then discover localization YAML recursively by `l_<locale>:` headers, filename suffixes, and language directories. Work from an extracted copy when the source is a ZIP.
2. Detect the language actually used by localization values. Do not trust a folder or header alone: Japanese text may be stored under `l_english`. Classify a valid script-only mod with no natural-language localization as `Non-linguistic`, and never send it to an LLM.
3. Confirm the detected or explicitly selected source language, source CK3 locale, target language, target CK3 locale, provider, endpoint, and model. Disable translation when the detected source language already equals the target language. Remote keys may be stored only in Windows Credential Manager; never place them in scripts, settings JSON, SQLite, logs, manifests, or generated mods.
4. Read [references/ck3-localization.md](references/ck3-localization.md) before changing localization files. Read [references/providers.md](references/providers.md) before configuring a remote API. Read [references/agent-compatibility.md](references/agent-compatibility.md) when packaging or installing the skill for another agent.
5. Stage translations outside the live mod directory. Reuse the same SQLite cache on every run so completed entries survive interruptions and retries.
6. For ordinary Windows users, prefer `dist/CK3_Mod_Translator.exe` when the packaged app is available; otherwise run `python scripts/ck3_gui.py`. Scan a mod library or add individual roots/descriptors, review the detected languages, check only the intended mods, and create translated clones. The app installs each clone and launcher descriptor into the CK3 local-mod directory automatically.
7. For automation, run `scripts/ck3_clone.py <mod-root>` with explicit source/target language options when auto-detection is not being used. Prefer 4 workers locally and conservative parallelism for paid APIs.
8. Do not install or publish unless file count, keys, CK3 tokens, headers, UTF-8 BOM, placeholders, single-line values, and non-localization copy hashes all pass.
9. Review terminology and a sample from UI labels, game rules, decisions, events, and explicit prose. Supply a JSON glossary and rerun when recurring terms are inconsistent.
10. When the user wants one localization-only bundle from multiple mods, run `scripts/ck3_pipeline.py` with a reviewed local-only JSON configuration.
11. Keep `collision_policy` set to `error` unless the user has reviewed a conflicting duplicate key and explicitly chosen `first` or `last`.
12. Run the `install` subcommand only after validation when installing translations into each source mod instead of creating a complete clone.
13. Validate the generated or installed tree again and report source/target languages, counts, cache path, backup path, provider, model, and any entries still requiring review.

## Commands

Launch the end-user GUI or create a complete clone from one path:

```powershell
dist\CK3_Mod_Translator.exe
python scripts\ck3_gui.py
python scripts/ck3_clone.py "C:\path\to\Mod"
python scripts/ck3_mod_scanner.py "C:\path\to\CK3\mod"
```

Translate one or more mods:

```powershell
python scripts/ck3_localize.py translate `
  --mod "Carnalitas=C:\path\to\Carnalitas" `
  --mod "Another Mod=C:\path\to\Another Mod" `
  --output "C:\work\ck3-ja" `
  --cache "C:\work\ck3-ja.sqlite" `
  --source-language English --source-locale l_english `
  --language Japanese --locale l_japanese `
  --endpoint http://127.0.0.1:1234/v1/chat/completions `
  --model local-model-id --workers 4
```

Validate without contacting the model:

```powershell
python scripts/ck3_localize.py validate --mod "Carnalitas=C:\path\to\Carnalitas" --output "C:\work\ck3-ja" --source-language English --source-locale l_english --language Japanese --locale l_japanese
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
- Never translate a mod merely because its directory or header says `english`; use actual-text detection, and require an explicit override when detection is uncertain.
- Do not equate visual-age mods with immortality when handling adjacent CK3 mod setup.
- Do not mark the task complete while the cache has failed or pending entries.
- Treat a model refusal, English copy, Simplified Chinese leakage in Japanese output, JSON residue, runaway vocalization, or actual newline inside a value as a failed translation.
