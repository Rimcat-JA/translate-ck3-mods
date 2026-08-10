# Agent compatibility

The skill uses a portable `SKILL.md` plus standard Python and has no Codex-only runtime dependency.

- Codex: copy the complete `translate-ck3-mods` folder to `~/.codex/skills/translate-ck3-mods`.
- Claude Code: copy the same folder to `~/.claude/skills/translate-ck3-mods`. Claude ignores `agents/openai.yaml`; it can still read `SKILL.md`, `scripts`, and `references`.
- Project-scoped use: place the folder in the agent's project skill directory when global installation is not desired.

Keep the folder intact so relative references and scripts continue to resolve. Configure the local LLM endpoint and model at execution time; do not distribute machine-specific paths, model files, caches, or credentials with the skill.
