# Translation providers

The desktop app defaults to `local`. Remote providers are opt-in and may charge for requests.

| Provider id | Chat-completions endpoint | Models endpoint |
|---|---|---|
| `local` | User-configurable loopback `/v1/chat/completions` | Same loopback server: `/api/v1/models`, `/v1/models`, then `/api/v0/models` |
| `openai` | `https://api.openai.com/v1/chat/completions` | `https://api.openai.com/v1/models` |
| `openrouter` | `https://openrouter.ai/api/v1/chat/completions` | `https://openrouter.ai/api/v1/models` |
| `nanogpt` | `https://nano-gpt.com/api/v1/chat/completions` | `https://nano-gpt.com/api/v1/models` |
| `nanogpt_subscription` | `https://nano-gpt.com/api/subscription/v1/chat/completions` | `https://nano-gpt.com/api/v1/models` |

Authoritative documentation:

- [LM Studio API overview](https://lmstudio.ai/docs/developer/rest)
- [LM Studio native v1 model list](https://lmstudio.ai/docs/developer/rest/list)
- [LM Studio legacy v0 REST API](https://lmstudio.ai/docs/developer/rest/endpoints)
- [LM Studio API-token authentication](https://lmstudio.ai/docs/developer/core/authentication)
- [LM Studio server and JIT settings](https://lmstudio.ai/docs/developer/core/server/settings)
- [OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat)
- [OpenRouter quickstart](https://openrouter.ai/docs/quickstart)
- [NanoGPT Chat Completion](https://docs.nano-gpt.com/api-reference/endpoint/chat-completion)
- [NanoGPT Models](https://docs.nano-gpt.com/api-reference/endpoint/models)

## Local LM Studio discovery

- Derive model-list URLs from the scheme, host, and port of the configured loopback chat endpoint. Do not copy a machine-specific path or assume the default port.
- Try LM Studio's native v1 list (`/api/v1/models`) first, then the OpenAI-compatible list (`/v1/models`), and finally the legacy native list (`/api/v0/models`). Native v1 uses `models[].key`; the compatible and legacy forms use `data[].id`. Native v1 is preferred because its type metadata lets the app exclude embedding-only models reliably.
- Offer chat-capable LLM/VLM entries, not embedding-only models, when the response contains type metadata.
- Keep the model field editable. An empty or unavailable list endpoint does not prove that a model identifier is unusable: LM Studio can load a downloaded model when the first chat request arrives if JIT loading is enabled.
- If discovery is empty, ask the user to check that the Developer server is running, confirm the endpoint host and port, import/download a chat model, and load it or enable JIT. The user may then paste the exact model identifier manually.
- LM Studio normally needs no token. When **Require Authentication** is enabled, accept an optional local API token for both discovery and translation. Persist it only in Windows Credential Manager.

## Security rules

- `scripts/ck3_providers.py` must validate the provider and exact endpoint before an Authorization header is created.
- Remote endpoints require HTTPS and reject query strings, fragments, embedded credentials, alternate hosts, and alternate paths.
- The GUI asks for confirmation before every remote translation run.
- API keys and the optional local LM Studio token are kept in memory during a run. Optional persistence uses Windows Credential Manager.
- Never put a key in settings JSON, logs, SQLite translation memory, manifests, generated mods, command arguments, tests, or repository files.
- Operational logs may contain timestamps, paths, counts, provider ids, model ids, and error summaries. They must not contain source/translated values or Authorization headers.
