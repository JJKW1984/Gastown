# Configuration Reference

This document covers every configuration option for Gastown, how values are resolved, and recommended setups for common scenarios.

---

## Contents

- [How Configuration is Resolved](#how-configuration-is-resolved)
- [Core Variables](#core-variables)
- [LLM Provider Credentials](#llm-provider-credentials)
- [Example Setups](#example-setups)
- [Troubleshooting Configuration Issues](#troubleshooting-configuration-issues)

---

## How Configuration is Resolved

Gastown uses `python-dotenv` to load a `.env` file at startup (CLI only; the web app reads the environment directly). Resolution order, highest priority first:

1. **Shell environment variables** (`export GASTOWN_MODEL=…`)
2. **`.env` file** in the current working directory
3. **Built-in defaults** (documented in the table below)

The CLI calls `load_dotenv()` at module import. The web app (`uvicorn`) reads the environment at process start; use `uvicorn --env-file .env …` or set variables before launching if you are not using `gastown serve`.

---

## Core Variables

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `GASTOWN_DB_PATH` | `gastown.db` | string | Path to the SQLite database file. Relative paths are resolved from the current working directory. |
| `GASTOWN_HOST` | `127.0.0.1` | string | Hostname or IP to bind the web server. Use `0.0.0.0` in containers. |
| `GASTOWN_PORT` | `8000` | integer | Port for the web server. |
| `GASTOWN_MAX_CONCURRENT_POLECATS` | `4` | integer | Maximum number of PoleCAT worker tasks running simultaneously. If set, this environment variable overrides the per-run `max_concurrent` field in `POST /api/runs`; if unset, the per-run value (or the built-in default of `4`) is used. |
| `GASTOWN_STUCK_TIMEOUT_SECONDS` | `120` | integer | Seconds of heartbeat silence before the Witness nudges a PoleCAT. After 3 nudges with no progress the bead is cancelled. |
| `GASTOWN_MODEL` | `anthropic/claude-sonnet-4-6` | string | LiteLLM model string. Format: `<provider>/<model-name>`. |

---

## LLM Provider Credentials

Gastown uses [LiteLLM](https://github.com/BerriAI/litellm) as its LLM abstraction layer. Set one of the provider blocks below and set `GASTOWN_MODEL` accordingly.

### Anthropic (default)

```bash
ANTHROPIC_API_KEY=sk-ant-api03-...
GASTOWN_MODEL=anthropic/claude-sonnet-4-6
```

Available model strings:

| String | Notes |
|--------|-------|
| `anthropic/claude-sonnet-4-6` | Default; good balance of speed and quality |
| `anthropic/claude-opus-4-5` | Highest quality; slower and more expensive |
| `anthropic/claude-haiku-4-5` | Fastest; lowest cost |

---

### OpenAI

```bash
OPENAI_API_KEY=sk-...
GASTOWN_MODEL=openai/gpt-4o
```

Available model strings:

| String | Notes |
|--------|-------|
| `openai/gpt-4o` | Recommended |
| `openai/gpt-4o-mini` | Lower cost |
| `openai/gpt-4-turbo` | Previous generation |

---

### Azure OpenAI

```bash
AZURE_API_KEY=...
AZURE_API_BASE=https://your-resource.openai.azure.com
AZURE_API_VERSION=2024-02-01
GASTOWN_MODEL=azure/<your-deployment-name>
```

Replace `<your-deployment-name>` with the name you used when deploying the model in Azure AI Studio.

---

### Ollama (local, no API key)

```bash
OLLAMA_API_BASE=http://localhost:11434    # default; set only if Ollama runs elsewhere
GASTOWN_MODEL=ollama/llama3
```

Ollama must be running before you start Gastown. Download a model first:

```bash
ollama pull llama3
```

Any model available in your Ollama instance can be used. Tool-call support varies by model; use `llama3`, `mistral-nemo`, or `qwen2.5-coder` for best results.

---

## Example Setups

### Budget setup (local Ollama)

```bash
# .env
GASTOWN_MODEL=ollama/llama3
GASTOWN_MAX_CONCURRENT_POLECATS=2
GASTOWN_STUCK_TIMEOUT_SECONDS=300    # local models are slower
GASTOWN_DB_PATH=/home/user/.gastown/gastown.db
```

### Quality setup (Anthropic Opus)

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
GASTOWN_MODEL=anthropic/claude-opus-4-5
GASTOWN_MAX_CONCURRENT_POLECATS=2    # Opus is slower; fewer concurrent saves cost
GASTOWN_STUCK_TIMEOUT_SECONDS=180
```

### Production / Azure Web App

```bash
# Set as Azure Web App application settings (not in .env)
GASTOWN_HOST=0.0.0.0
GASTOWN_PORT=8000
GASTOWN_DB_PATH=/mnt/data/gastown.db       # persistent volume
GASTOWN_MAX_CONCURRENT_POLECATS=8
GASTOWN_STUCK_TIMEOUT_SECONDS=120
GASTOWN_MODEL=azure/gpt-4o-deployment
AZURE_API_KEY=...
AZURE_API_BASE=https://your-resource.openai.azure.com
AZURE_API_VERSION=2024-02-01
```

See `docs/azure-webapp-terraform-deploy.md` for how Terraform provisions these settings automatically.

### High-throughput setup (OpenAI with increased concurrency)

```bash
# .env
OPENAI_API_KEY=sk-...
GASTOWN_MODEL=openai/gpt-4o
GASTOWN_MAX_CONCURRENT_POLECATS=16
GASTOWN_STUCK_TIMEOUT_SECONDS=90
```

Note: increasing `GASTOWN_MAX_CONCURRENT_POLECATS` beyond your provider's rate limit will cause 429 errors. OpenAI tier 5 supports ~50 RPM by default; adjust concurrency accordingly.

---

## Troubleshooting Configuration Issues

### `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` not found

```
litellm.exceptions.AuthenticationError: ...
```

1. Confirm the key is exported: `echo $ANTHROPIC_API_KEY`
2. If using `.env`, confirm `load_dotenv()` ran (it runs on CLI import; web app needs the key in the shell environment).
3. Check for leading/trailing spaces in the `.env` value: `ANTHROPIC_API_KEY= sk-...` (space before `sk` is included in the value).

### Wrong model string format

```
litellm.exceptions.NotFoundError: LiteLLM: Model Not Found
```

LiteLLM model strings are `<provider>/<model-name>`. Common mistakes:

| Wrong | Correct |
|-------|---------|
| `claude-sonnet-4-6` | `anthropic/claude-sonnet-4-6` |
| `gpt-4o` | `openai/gpt-4o` |
| `azure-gpt4` | `azure/<your-deployment-name>` |

### `GASTOWN_MAX_CONCURRENT_POLECATS` has no effect

The orchestrator reads this env var at run time via `os.getenv()`, not at construction time. Setting it after the process starts has no effect on the current run. Restart the server or CLI session.

### Database path not found

```
sqlite3.OperationalError: unable to open database file
```

The directory containing the database file must exist. Gastown does not create parent directories automatically:

```bash
mkdir -p /mnt/data
export GASTOWN_DB_PATH=/mnt/data/gastown.db
```

### Ollama connection refused

```
httpx.ConnectError: [Errno 111] Connection refused
```

Ollama is not running. Start it:

```bash
ollama serve &
```

Or if Ollama runs on a different machine:

```bash
OLLAMA_API_BASE=http://192.168.1.100:11434
```
