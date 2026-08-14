# Translation providers

The desktop app defaults to `local`. Remote providers are opt-in and may charge for requests.

| Provider id | Chat-completions endpoint | Models endpoint |
|---|---|---|
| `local` | User-configurable loopback `/v1/chat/completions` | Same loopback server `/v1/models` |
| `openai` | `https://api.openai.com/v1/chat/completions` | `https://api.openai.com/v1/models` |
| `openrouter` | `https://openrouter.ai/api/v1/chat/completions` | `https://openrouter.ai/api/v1/models` |
| `nanogpt` | `https://nano-gpt.com/api/v1/chat/completions` | `https://nano-gpt.com/api/v1/models` |
| `nanogpt_subscription` | `https://nano-gpt.com/api/subscription/v1/chat/completions` | `https://nano-gpt.com/api/v1/models` |

Authoritative documentation:

- [OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat)
- [OpenRouter quickstart](https://openrouter.ai/docs/quickstart)
- [NanoGPT Chat Completion](https://docs.nano-gpt.com/api-reference/endpoint/chat-completion)
- [NanoGPT Models](https://docs.nano-gpt.com/api-reference/endpoint/models)

## Security rules

- `scripts/ck3_providers.py` must validate the provider and exact endpoint before an Authorization header is created.
- Remote endpoints require HTTPS and reject query strings, fragments, embedded credentials, alternate hosts, and alternate paths.
- The GUI asks for confirmation before every remote translation run.
- API keys are kept in memory during a run. Optional persistence uses Windows Credential Manager.
- Never put a key in settings JSON, logs, SQLite translation memory, manifests, generated mods, command arguments, tests, or repository files.
- Operational logs may contain timestamps, paths, counts, provider ids, model ids, and error summaries. They must not contain source/translated values or Authorization headers.
