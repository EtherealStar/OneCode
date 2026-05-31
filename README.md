# OneCode

OneCode is a minimal CLI code agent MVP. It runs an agent loop that calls tools, feeds tool results back to the model, and stops when the model no longer requests tools.

## Requirements

- Python 3.11+
- `uv`
- A custom server that implements the Chat Completions tool-calling protocol

## Install Environment

Install `uv` first if it is not already available:

```powershell
pip install uv
```

Create the virtual environment and install the project:

```powershell
uv venv
uv pip install -e ".[test]"
```

Configure environment variables:

```powershell
$env:ONECODE_BASE_URL="https://your-chat-completions-server"
$env:ONECODE_API_KEY="your_api_key"
$env:ONECODE_MODEL="your-model-name"
```

Optional configuration:

```powershell
$env:ONECODE_CONTEXT_WINDOW="200000"
$env:ONECODE_MAX_OUTPUT_TOKENS="8000"
$env:ONECODE_ESCALATED_MAX_OUTPUT_TOKENS="64000"
```

## Run

Interactive mode:

```powershell
uv run onecode
```

Single prompt mode:

```powershell
uv run onecode "Read README.md and summarize the project"
```

Useful interactive commands:

- `/tools`: list enabled tools
- `/compact`: manually compact conversation context
- `/clear`: clear current session messages
- `/exit`: exit

## Test

```powershell
uv run pytest
```

The MVP includes tests for the core loop, serial tool execution, hook blocking, tool result compaction, rate-limit retry, output-token recovery, and context-limit recovery.

## API Protocol

The built-in client sends HTTP requests directly to:

```text
{ONECODE_BASE_URL}/v1/chat/completions
```

If `ONECODE_BASE_URL` already ends with `/v1`, it sends to:

```text
{ONECODE_BASE_URL}/chat/completions
```

Tool schemas are sent using Chat Completions function/tool-calling format. No provider SDK is required.

`ONECODE_BASE_URL` is required. OneCode does not default to any public provider endpoint.
