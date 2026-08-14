# CK3 localization rules

## File layout and language discovery

- Localization may live under `<mod>/localization/<language>/**/*.yml`, `<mod>/localization/replace/<language>/**/*.yml`, `<mod>/localization/<language>/replace/**/*.yml`, or framework-specific directories such as `replace/clausewitz`.
- Discover the stored locale from the first `l_<locale>:` header, then the `_l_<locale>.yml` suffix, then a recognized language path segment. Do not assume every source is English.
- Detect the natural language of localization values separately from the stored locale. A file headed `l_english:` can contain Japanese, and the actual text language must prevent accidental Japanese-to-Japanese retranslation.
- A valid mod with no natural-language localization is `Non-linguistic`; do not call an LLM for it. Missing or unresolved descriptors, archives, and malformed mod roots are invalid rather than translation candidates.
- Place a translation under the corresponding target-language path, rename `_l_<source>.yml` to `_l_<target>.yml`, and use `l_<target>:` as the first line.
- Preserve relative subdirectories other than recognized language components. Some mods omit the source header or contain a small number of malformed closing quotes; repair the generated target without rewriting the source archive.
- CK3 expects UTF-8 with BOM.

## Protected syntax

Preserve these byte-for-byte and with the same multiplicity:

- Escapes: `\n`, `\t`, `\r`
- Scripted values and concepts: `[Character.GetFirstName]`, `[Concept('key','text')|E]`
- Variables: `$VALUE$`
- Icons: `@gold_icon!`
- Formatting: `#EMP`, `#BOLD`, `#color_red`, `#!`
- Script blocks: `{ ... }`

Mask protected syntax before asking a model to translate. Reject output when placeholder counts or protected-token counters differ.

## Quality checks

- Compare source and target file sets and key sets exactly.
- Reject replacement characters, internal model placeholders, Markdown fences, JSON fragments, and physical CR/LF characters inside a localization value.
- Reject a single kana or letter repeated more than 20 times. Compact source vocalizations before translation.
- Apply target-aware validation. For Japanese, require kana or CJK in translated prose and reject unmistakable Simplified Chinese characters. For a non-Latin source translated into a Latin-script language, reject unchanged source-script output.
- Allow ASCII in proper names, debug variable names, mod acronyms, and scripted tokens.
- Spot-check titles, descriptions, options, game concepts, and long events. A syntactically valid translation can still use inconsistent terminology.

## Safe installation

1. Resolve the absolute mod root and target locale path.
2. Confirm the target is strictly inside the intended mod root.
3. Move an existing locale directory to `<mod>/_translation_backups/<locale>_<timestamp>`.
4. Copy the staged locale directory into `<mod>/localization/<locale>`.
5. Validate the installed files against the selected source-locale files.

Keep backups outside `localization` to prevent duplicate keys.

## Standalone bundle rules

- The local LLM translates values only. Use deterministic code for file discovery, merging, conflicts, descriptors, manifests, ZIPs, and backups.
- Default to a loopback endpoint. A hosted provider is allowed only after explicit user selection, and remote keys may be sent only to the exact official HTTPS endpoints in `scripts/ck3_providers.py`.
- Deduplicate identical repeated keys. Stop on different values for the same key unless the user has explicitly selected a reviewed first/last policy.
- Emit one consolidated locale file per source mod with stable ordering, one `descriptor.mod` inside the mod, and one launcher `.mod` beside it.
- Record relative source filenames, SHA-256 hashes, counts, collision decisions, and output hashes in a manifest. Do not record API keys or absolute machine paths.
- Sort ZIP entries and use fixed timestamps so identical input produces identical archives.
- Never silently replace an existing bundle. Move every existing artifact to `_bundle_backups` before an authorized overwrite.
