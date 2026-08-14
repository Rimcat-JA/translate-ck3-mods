# Agent compatibility

The skill uses a portable `SKILL.md` plus standard Python and has no Codex-only runtime dependency.

- Codex: copy `SKILL.md`, `agents`, `scripts`, and `references` to `~/.codex/skills/translate-ck3-mods`.
- Claude Code: copy `SKILL.md`, `scripts`, and `references` to `~/.claude/skills/translate-ck3-mods`. `agents/openai.yaml` is harmless but not required by Claude.
- Project-scoped use: place the folder in the agent's project skill directory when global installation is not desired.

Keep those relative directories intact. User documentation, tests, packaging sources, build artifacts, and the optional EXE belong to the software repository or release package rather than the installed agent skill. Configure the LLM endpoint and model at execution time; do not distribute machine-specific paths, model files, caches, or credentials with the skill.
