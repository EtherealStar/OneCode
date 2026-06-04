# Implement glob and grep tools

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `PLANS.md` in the repository root. It is self-contained so a contributor can implement the feature from this document and the current working tree.

## Purpose / Big Picture

After this change, OneCode agents can discover files by glob pattern and search file contents with ripgrep through first-class runtime tools. The model will use `glob` when it needs filenames matching patterns such as `**/*.py`, and `grep` when it needs regex search across file contents. Both tools will be registered like existing `read_file` and `edit_file`, protected by the sandbox guard, visible through the dynamic tool registry, and covered by focused tests.

This plan intentionally does not implement `ask_user_question`. That tool depends on the future UI interaction layer and will be planned after the UI can present questions and collect answers.

## Progress

- [x] (2026-06-04 10:45Z) Read `AGENTS.md`, `PLANS.md`, `architecture.md`, `docs/design-docs/tool-design-guidelines.md`, active plans, tech debt, current file tools, and reference `GlobTool`, `GrepTool`, and `AskUserQuestionTool`.
- [x] (2026-06-04 10:45Z) Recorded user decisions: tool names use snake_case; `grep` uses ripgrep; search results should be filtered through guard with caching for performance; `grep` should support the reference fields; `ask_user_question` is excluded until UI exists.
- [ ] Extend the shared tool input validator enough to enforce the new strict schemas.
- [ ] Add the `tools/glob/` descriptor, prompt, implementation, and tests.
- [ ] Add the `tools/grep/` descriptor, prompt, ripgrep runner, implementation, and tests.
- [ ] Add registry prompt-section support if it is still missing.
- [ ] Update architecture and tech debt documentation for the new tools and any remaining limitations.
- [ ] Run compile checks and focused/full tests.

## Surprises & Discoveries

- Observation: OneCode is a Python project, while the reference tools use TypeScript and Zod.
  Evidence: `pyproject.toml` only defines Python dependencies, and existing tool descriptors use JSON Schema dictionaries consumed by `services/tools/executor.py`. The plan therefore uses strict JSON Schema plus an expanded internal validator instead of adding the JavaScript `zod` package.

- Observation: Current shared schema validation is intentionally small.
  Evidence: `services/tools/executor.py` currently validates required fields, unexpected fields, `string`, `boolean`, and `integer` with `minimum`. It does not yet validate `enum`, arrays, nested objects, number types, max/min array lengths, or object maps. `grep` and `ask_user_question` reference schemas need richer validation, but this plan only needs the subset required by `glob` and `grep`.

- Observation: The executor already applies `ToolResultPolicy`.
  Evidence: `RegistryToolExecutor._apply_result_policy()` turns oversized result content into a JSON preview with truncation metadata. `grep` can rely on this for the 20KB result budget even though durable result store is still a future capability.

- Observation: Guard currently handles filesystem targets only when `ToolTarget.kind` is `file` or `directory`.
  Evidence: `RegistryToolExecutor._check_guard()` skips non-filesystem target kinds, and accepts operations `read`, `write`, `list`, and `delete`. `glob` and `grep` should therefore classify their root access as `directory/list`, `directory/read`, or `file/read`, then do extra per-result filtering in the handler.

## Decision Log

- Decision: Provider-visible tool names will be `glob` and `grep`.
  Rationale: The tool design guideline requires snake_case names that match registry keys and tool directories. The TypeScript reference names `Glob` and `Grep` are treated as source material, not OneCode naming.
  Date/Author: 2026-06-04 / User and Codex

- Decision: `grep` will execute the `rg` ripgrep binary through a small Python runner wrapper.
  Rationale: The reference tool is built on ripgrep, and the user explicitly requested ripgrep. A wrapper isolates subprocess details and makes tests easy to fake.
  Date/Author: 2026-06-04 / User and Codex

- Decision: `glob` will use Python filesystem traversal and `fnmatch`, not shell glob expansion.
  Rationale: Python traversal keeps behavior cross-platform, avoids shell injection concerns, and lets the handler filter each candidate through the sandbox guard before returning it.
  Date/Author: 2026-06-04 / Codex

- Decision: Search handlers will filter candidate result paths through the guard, with per-call caching keyed by normalized absolute path and operation.
  Rationale: Guarding only the root directory could leak denied filenames from broad searches. Per-result guard checks prevent leakage, while a call-local cache avoids repeated boundary classification for duplicate paths in content and count modes.
  Date/Author: 2026-06-04 / User and Codex

- Decision: The plan will not introduce JavaScript Zod into the Python runtime.
  Rationale: The user allowed Zod-style validation, but the current runtime is Python and already uses JSON Schema-shaped descriptors. The compatible approach is to expand the internal validator to enforce the new schemas strictly.
  Date/Author: 2026-06-04 / Codex

- Decision: `ask_user_question` is explicitly out of scope.
  Rationale: The user wants to implement the UI first. The question tool requires a UI/callback contract for presenting options, adding an automatic "Other" choice, and returning answers without hanging the agent loop.
  Date/Author: 2026-06-04 / User

## Outcomes & Retrospective

Not implemented yet. Update this section after implementation with the final behavior, test results, and any limitations discovered while integrating ripgrep or guard filtering.

## Context and Orientation

OneCode is a Python code-agent runtime. The main loop in `core/loop.py` is intentionally thin: it appends user messages, builds a `ContextSnapshot`, calls the model, executes any tool calls through the injected tool executor, appends tool results, and repeats until completion. New tools must not be hard-coded into the loop.

The tool runtime lives under `services/tools/`. `services/tools/types.py` defines `ToolDescriptor`, `ToolCallClassification`, `ToolTarget`, `ToolResultPolicy`, and `ToolRuntime`. A concrete tool lives under `tools/<tool_name>/` and exports `descriptor()` from `tool.py`; its prompt text lives in `prompt.py`.

`services/tools/registry.py` owns enabled tool descriptors. It already returns provider-visible OpenAI-compatible schemas through `tool_schemas(state)`. If prompt-section support is still missing when this plan is implemented, add `tool_prompt_sections(state) -> tuple[str, ...]` returning non-empty descriptor prompts in stable descriptor order. This should not assemble the whole system prompt; it only exposes tool prompt text for the future prompt assembler.

`services/tools/executor.py` owns the execution pipeline. It looks up the descriptor, validates input shape, runs tool-level validation, classifies the call, checks the sandbox guard for filesystem targets, runs hooks, invokes the handler, applies result policy, and emits structured errors. Do not bypass this pipeline from new tools.

`services/guard/` owns path safety. `SandboxGuard.check_path()` classifies a path as allowed, ask-required, or denied. The executor already checks declared `ToolTarget`s before handler execution. Broad search tools need an additional handler-level filtering step because the target root can be allowed while individual discovered paths match deny rules.

`tools/read_file/` and `tools/edit_file/` are the local implementation pattern. Each has `__init__.py`, `tool.py`, and `prompt.py`; each descriptor includes a strict input schema, a prompt string, a search hint, a validator, an input-aware classifier, and a handler.

The reference TypeScript tools live in `docs/references/Tools_full/GlobTool/` and `docs/references/Tools_full/GrepTool/`. They establish the desired product behavior: `Glob` finds files by wildcard pattern and sorts by modification time; `Grep` uses ripgrep with output modes `content`, `files_with_matches`, and `count`, supports file filters and context flags, defaults to bounded output, and uses result-size budgeting.

## Plan of Work

First, extend shared schema validation in `services/tools/executor.py`. Keep it intentionally small, but add the JSON Schema subset required by the new tools: `enum`, `number`, arrays with `items`, `minItems`, and `maxItems`, nested `object` validation, and `additionalProperties` as either `False` or a schema for string-keyed maps. Keep error messages deterministic and short. Existing tests for validation must continue to pass. Add tests for each new supported validator feature in `tests/test_tool_registry_and_executor.py`.

Second, add registry prompt-section support if it is not already present. Edit `services/tools/registry.py` to expose `tool_prompt_sections(state)`. Return prompts in the same stable descriptor order as `descriptors()`, skip empty prompt strings, and do not add policy logic that belongs to permission/enablement work. Add a test proving prompts follow stable descriptor order and skip empty prompts.

Third, add `tools/glob/`. Create `tools/glob/__init__.py`, `tools/glob/prompt.py`, and `tools/glob/tool.py`. The descriptor name is `glob`, description is a short sentence, search hint is `find files by name pattern`, and result policy is 100KB with preview around 4000 chars. The input schema is a strict object:

    {
      "pattern": "string, required",
      "path": "string, optional",
      "head_limit": "integer >= 0, optional",
      "offset": "integer >= 0, optional"
    }

`path` defaults to the sandbox cwd. `head_limit` defaults to 100; explicit `0` means unlimited and should be used sparingly. `offset` defaults to 0.

The `glob` validator should reject empty patterns, negative limits, and a provided path that resolves to an allowed existing non-directory. If the provided path is outside the sandbox, do not stat it directly in the validator; let the classification and executor guard return the structured ask/deny result. The classifier returns `read_only=True`, `modifies_filesystem=False`, `concurrency_safe=True`, one target `ToolTarget(kind="directory", operation="list", value=path_or_dot)`, and permission subject `glob:<path>:<pattern>`.

The `glob` handler should require `runtime.guard`. It should run under the normalized allowed root from `guard.check_path(root, operation="list", kind="directory")`. It should recursively enumerate files under the root, match paths against the glob pattern using paths relative to the search root and normalized slash separators, skip directories from results, and collect matching files. Sort matches by modification time descending with path as a deterministic tiebreaker. Apply offset and head limit after sorting. Convert returned filenames to paths relative to `runtime.guard.boundary.cwd` when possible, again using slash separators; otherwise return absolute normalized paths. Filter each candidate through `guard.check_path(candidate, operation="read", kind="file")` before it is returned. Use a call-local cache so repeated candidate paths are classified once. The content returned to the model is one filename per line, with a short truncation/pagination note if a limit was applied. Metadata should include `num_files`, `total_matches_before_pagination`, `filtered_count`, `applied_limit`, `applied_offset`, `truncated`, and `path`.

Fourth, add `tools/grep/`. Create `tools/grep/__init__.py`, `tools/grep/prompt.py`, and `tools/grep/tool.py`. The descriptor name is `grep`, description is a short sentence, search hint is `search file contents with regex`, and result policy is 20KB with preview around 4000 chars. The strict input schema should support the reference fields:

    pattern: string, required
    path: string, optional
    glob: string, optional
    output_mode: enum ["content", "files_with_matches", "count"], optional
    -B: integer >= 0, optional
    -A: integer >= 0, optional
    -C: integer >= 0, optional
    context: integer >= 0, optional
    -n: boolean, optional
    -i: boolean, optional
    type: string, optional
    head_limit: integer >= 0, optional
    offset: integer >= 0, optional
    multiline: boolean, optional

Defaults should match the reference intent: `output_mode` defaults to `files_with_matches`; `head_limit` defaults to 250; explicit `head_limit=0` means unlimited; `offset` defaults to 0; `-n` defaults to true only for `content`; `-i` and `multiline` default to false.

The `grep` validator should reject empty patterns; reject negative numeric fields; reject context flags used with non-`content` modes if this makes the user intent ambiguous, or ignore them consistently and record ignored fields in metadata. Prefer rejecting ambiguous input with a helpful validation message. If both `context` and `-C` are provided, prefer `context` and record that decision in the implementation comments and tests. `path` may point to a file or directory; if it is outside sandbox, let executor guard produce ask/deny instead of doing a direct stat first.

The `grep` classifier should return `read_only=True`, `modifies_filesystem=False`, `concurrency_safe=True`, and a filesystem read target. If `path` is absent, the target is `ToolTarget(kind="directory", operation="read", value=".")`. If `path` is present, classification can use `kind="directory"` by default because the guard will safely classify the path; the handler can later detect file versus directory for ripgrep. The permission subject is `grep:<path_or_dot>:<pattern>`.

The `grep` handler should require `runtime.guard`, check the search path through `guard.check_path(path_or_dot, operation="read", kind="directory")`, and then call ripgrep through a wrapper. The wrapper can be a small private function in `tools/grep/tool.py` or a testable class in the same module. Use `subprocess.run()` with an argument list, not shell strings. Always include flags that reduce noisy output: `--hidden`, `--max-columns 500`, and excludes for VCS directories `.git`, `.svn`, `.hg`, `.bzr`, `.jj`, and `.sl`. For `files_with_matches`, add `-l`; for `count`, add `-c`; for `content`, add line numbers if `-n` is true. For multiline, add `-U` and `--multiline-dotall`. For case-insensitive search, add `-i`. For `type`, add `--type <type>`. For `glob`, split simple whitespace-separated patterns while preserving brace patterns as much as practical; pass each as `--glob <pattern>`. If the regex pattern begins with `-`, pass it with `-e <pattern>`.

Handle ripgrep return codes explicitly. Return code 0 means matches found. Return code 1 means no matches and should produce a non-error result saying no files or matches were found. Return code 2 or missing executable should produce a structured tool error with metadata error codes such as `ripgrep_error` or `ripgrep_not_found`. Do not let subprocess exceptions escape the handler.

For `files_with_matches`, parse ripgrep output as paths, filter each path through guard read checks using a per-call cache, sort by modification time descending with path as a tiebreaker, then apply offset/head_limit. Return `Found N files` followed by one path per line, relative to workspace where possible. For `content`, parse each result line to identify the path prefix, filter by that path through the same cache, relativize the path prefix, then apply offset/head_limit to output lines. For `count`, parse `path:count` lines, filter paths, relativize them, apply offset/head_limit, and compute totals from the shown lines. Metadata should include mode, num files, num lines or matches where relevant, filtered count, applied limit, applied offset, and whether truncation occurred.

Fifth, add tests for the new tools. Prefer focused tests in a new file `tests/test_search_tools.py`. Use `tmp_path` workspaces and `SandboxGuard(SandboxBoundary(cwd=workspace, denied_patterns=...))`. Tests should cover descriptor schema projection, classification, prompt-section exposure, invalid inputs, guard deny/ask behavior, no handler execution on blocked root paths, denied-result filtering, pagination, stable sorting, and each grep output mode. For ripgrep integration, first check whether `rg --version` is available. If available, run real subprocess tests. If not available, skip only the tests that require the executable and keep parser/handler unit tests using a fake runner or monkeypatch.

Sixth, update documentation. Edit `architecture.md` so the `tools/` section lists `glob` and `grep` as implemented, while `write_file`, `bash`, and `ask_user_question` remain future or out of scope. Update `docs/tech-debt/tech-debt-tracker.md` if implementation leaves known debt, such as no durable result store for oversized search results or no true concurrent execution despite the tools being marked concurrency-safe. Do not claim that full result-store or concurrency scheduling is solved unless the implementation actually changes those services.

## Concrete Steps

Run all commands from the repository root:

    cd D:\study\OneCode

Before editing, inspect the worktree so existing user changes are not overwritten:

    git status --short

Verify ripgrep availability:

    rg --version

If `rg --version` fails in the developer environment, continue implementing the tool so it returns `ripgrep_not_found` at runtime, and skip only real-ripgrep integration tests. Do not install external software inside this plan unless the user separately approves that environment change.

Edit files in this order:

1. Update `services/tools/executor.py` validator support and tests in `tests/test_tool_registry_and_executor.py`.
2. Update `services/tools/registry.py` with `tool_prompt_sections()` and tests.
3. Add `tools/glob/__init__.py`, `tools/glob/prompt.py`, and `tools/glob/tool.py`.
4. Add `tools/grep/__init__.py`, `tools/grep/prompt.py`, and `tools/grep/tool.py`.
5. Add `tests/test_search_tools.py`.
6. Update `architecture.md` and `docs/tech-debt/tech-debt-tracker.md`.

Focused validation during implementation:

    uv run python -m pytest tests/test_tool_registry_and_executor.py tests/test_search_tools.py -q

Compile check:

    uv run python -m compileall core services infrastructure tools

Full test suite:

    uv run python -m pytest tests -q

Manual smoke scenario after implementation:

    uv run python -m pytest tests/test_search_tools.py -q

Then, in a small Python snippet or future CLI/runtime assembly, register `read_file`, `edit_file`, `glob`, and `grep` descriptors in one `ToolRegistry`, create a `RegistryToolExecutor` with a workspace guard, and execute:

    ToolCall(id="call-glob", name="glob", input={"pattern": "**/*.py", "path": "."})
    ToolCall(id="call-grep", name="grep", input={"pattern": "ToolDescriptor", "path": ".", "glob": "*.py", "output_mode": "files_with_matches"})

The first call should return Python file paths. The second call should return files containing `ToolDescriptor`.

## Validation and Acceptance

Acceptance criterion one: `ToolRegistry.tool_schemas(RuntimeState())` includes `glob` and `grep` schemas when their descriptors are registered. Each schema has a snake_case function name, a short description, strict parameters, and no unexpected fields allowed.

Acceptance criterion two: `ToolRegistry.tool_prompt_sections(RuntimeState())`, if added by this plan, returns prompt text for `glob` and `grep` in stable descriptor order and skips tools with empty prompts.

Acceptance criterion three: invalid tool inputs fail before handler execution. Examples include missing `pattern`, non-string `pattern`, unsupported `output_mode`, negative `head_limit`, negative `offset`, non-boolean `-i`, and unexpected fields.

Acceptance criterion four: `glob` classifies calls as read-only, non-mutating, concurrency-safe, with a directory/list target and a 100KB result budget. It returns matching files sorted by modification time, paginates results, and records truncation and filtering metadata.

Acceptance criterion five: `grep` classifies calls as read-only, non-mutating, concurrency-safe, with a filesystem read target and a 20KB result budget. It supports `files_with_matches`, `content`, and `count` output modes, `glob`, `type`, line context fields, case-insensitive search, multiline mode, `head_limit`, and `offset`.

Acceptance criterion six: sandbox guard blocks unsafe root searches before handler execution. A denied or external `path` returns the existing structured guard error and does not enumerate files or execute ripgrep.

Acceptance criterion seven: broad allowed searches do not leak denied result paths. If a workspace contains `public.txt` and denied `secret.txt`, then `glob` and `grep` over `.` must not return `secret.txt`; metadata must show at least one filtered result when the denied file matched.

Acceptance criterion eight: ripgrep failures are structured tool results. No matches are non-error results. Missing `rg` executable, invalid regex, timeout if implemented, or ripgrep return code 2 are tool errors with useful metadata and do not escape as Python exceptions.

Acceptance criterion nine: result budget behavior works through the existing executor. A large `grep` result over 20KB becomes a non-error truncated preview with `result_truncated` metadata.

Acceptance criterion ten: these commands pass:

    uv run python -m compileall core services infrastructure tools
    uv run python -m pytest tests -q

## Idempotence and Recovery

All tests must use temporary workspaces and may create files only under `tmp_path`. Do not read or write real project files except for source files intentionally edited by this plan. Do not delete user files.

The new tools are read-only. They must never write to searched files, create indexes, modify metadata, or update runtime state except for returning result metadata. They may read file stats for sorting and filtering.

If ripgrep is unavailable, the `grep` tool should fail gracefully at runtime with a structured `ripgrep_not_found` error. Tests that require real ripgrep may be skipped, but unit tests for schema, classification, guard behavior, and output parsing must still pass.

Per-result guard filtering should be conservative. If a result path cannot be parsed, resolved, or classified, omit it from model-visible output and increment a filtered or skipped count in metadata. Do not return suspicious paths just because parsing failed.

The call-local guard cache should not persist across tool calls. Sandbox policy can change between calls, and persistent caches would risk stale permission decisions. A simple dictionary inside one handler invocation is enough.

## Artifacts and Notes

Example `glob` tool result content:

    Found 3 files
    core/loop.py
    services/tools/executor.py
    tools/read_file/tool.py

    [Showing results with pagination = limit: 3]

Example `grep` `files_with_matches` result content:

    Found 2 files
    services/tools/types.py
    tools/read_file/tool.py

Example `grep` `content` result content:

    services/tools/types.py:82:class ToolDescriptor:
    tools/read_file/tool.py:27:def descriptor() -> ToolDescriptor:

Example structured missing-ripgrep error content:

    {"error":"ripgrep_not_found","message":"ripgrep executable 'rg' was not found on PATH."}

## Interfaces and Dependencies

`tools/glob/tool.py` should expose:

    INPUT_SCHEMA: dict[str, Any]
    def descriptor() -> ToolDescriptor: ...

The handler returns a `ToolExecutionResult` whose `content` is model-readable text and whose metadata contains structured counts.

`tools/grep/tool.py` should expose:

    INPUT_SCHEMA: dict[str, Any]
    def descriptor() -> ToolDescriptor: ...

It may also define a small internal runner interface for tests:

    class RipgrepRunner(Protocol):
        def run(self, args: list[str], cwd: Path) -> RipgrepResult: ...

or equivalent functions:

    def _run_ripgrep(args: list[str], cwd: Path) -> RipgrepResult: ...

Choose the smallest design that allows tests to monkeypatch ripgrep output without invoking a real subprocess for every parser case.

No JavaScript Zod dependency should be added. The OneCode tool schema contract remains JSON Schema-shaped dictionaries in `ToolDescriptor.input_schema`; the executor validates the supported subset. If a future TypeScript UI wants Zod schemas, it can generate or mirror them from the descriptor contract in a separate plan.

The `pyproject.toml` dependency list does not need to change for ripgrep if the project uses the `rg` executable on PATH. If the team later wants vendored ripgrep distribution, that should be a separate environment/dependency plan because it affects installation and platform support.

2026-06-04 / Codex: Initial ExecPlan created after user decisions on snake_case names, ripgrep, guarded result filtering with caching, full grep field support, and postponing `ask_user_question` until UI exists.
