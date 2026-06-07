# AGENTS.md

## Purpose

OneCode is a Python code agent runtime. This file is the entry point for an agent working in this repository. It explains where project knowledge lives, the order to read it, where the code sits, and how to build, test, and extend the project.

This file is not an execution plan, architecture document, or task list. For target architecture, read `architecture.md`.

## Quick Start

1. Read `architecture.md` for the runtime structure, layers, core abstractions, dependency direction, and run flow.
2. Read the design docs in `docs/design-docs/` relevant to the area you will touch (start with `core-beliefs.md`).
3. Check `docs/exec-plans/active/` for in-progress or intended work that overlaps your task.
4. Check `docs/tech-debt/tech-debt-tracker.md` before changing code near known shortcuts.
5. Set up the environment (see "Environment Setup") and run the test suite to confirm a clean baseline before editing.

## First Reading Order

Before making project changes, read these sources in order:

1. `architecture.md`
   - Source of truth for target runtime structure, module boundaries, dependency direction, and overall flow.

2. `docs/design-docs/`
   - Design beliefs, conventions, and module-level architecture.
   - Explains the reasoning behind the system, not step-by-step implementation work.

3. `docs/exec-plans/active/`
   - Active execution plans for work that is intended, in progress, or still being shaped.
   - Check before implementing related behavior.

4. `docs/tech-debt/tech-debt-tracker.md`
   - Known shortcuts, accepted risks, and intended remediation directions.

5. `docs/references/`
   - Supporting context, examples, and external notes.
   - Does not override `architecture.md`, design docs, or active execution plans.

## Documentation Map

- `architecture.md`
  - Overall target architecture. Use it to decide where responsibilities belong and to find the per-module design doc for any area.

- `docs/design-docs/`
  - Conceptual and module-level design documents.
  - `core-beliefs.md`: design principles and anti-patterns that govern every change.
  - `tool-design-guidelines.md`: conventions for adding new tools.
  - `*-architecture.md`: per-module architecture (context, compaction, memory, tools, guard, permissions, hooks, subagents, skills, mcp, tasks, background tasks, model/provider, observability, cli).

- `docs/exec-plans/active/`
  - Currently active implementation plans. Reflects current direction.

- `docs/exec-plans/completed/`
  - Archived, already-implemented plans. Historical context only.

- `docs/tech-debt/tech-debt-tracker.md`
  - The technical debt tracker. Read it when working near known debt.

- `tech_debt_tracker_guide.md` (repository root)
  - Required fields, structure, and update rules for adding, changing, or resolving debt entries.

- `docs/references/`
  - Reference documents, notes, images, and topic-specific research (agent loops, tool use, permissions, hooks, context compaction, memory, system prompts, error recovery, task systems, and more).

- `PLANS.md` (repository root)
  - Requirements and format for authoring an ExecPlan.

## Dependency Boundaries

These constraints come from `architecture.md` and are checked by `tests/test_import_boundaries.py`:

- `core/loop.py` must not import concrete tool directories or concrete providers.
- `services/tools/` must not statically import top-level `tools/<tool_name>/`.
- `tools/` may depend on `services.tools` public types and `ToolRuntime`, but not on `core/loop.py`.
- `infrastructure/` must not depend on `core/`.
- `prompts/` may read tool descriptor prompt text but must not execute tools.
- A `guard` deny must not be overridable by hooks, session allow, permission prompts, or model requests.

## Environment Setup

OneCode uses `uv`.

- Sync the virtual environment: `uv sync --dev`.
- Activate on Windows: `.\.venv\Scripts\Activate.ps1`.
- Copy `.env.example` to `.env` for local model provider settings. OneCode reads model provider variables only from `.env`.

## Common Commands

- Run the full test suite: `uv run python -m pytest tests -q`.
- Run a single test file: `uv run python -m pytest tests/<file>.py -q`.
- Run compile checks: `uv run python -m compileall core services infrastructure`.
- Verify dependency boundaries after structural changes: `uv run python -m pytest tests/test_import_boundaries.py -q`.

## Working Guidance

- Keep this file concise and general. Do not add task-specific instructions here.
- Do not duplicate `architecture.md`; link to it instead.
- Before adding a capability, decide which layer it belongs to (tool, hook, prompt section, compaction layer, transition, model client adapter, or UI). Do not extend the main loop with capability-specific branches.
- Prefer active execution plans over completed plans when judging current implementation intent.
- Prefer the tech debt tracker over ad hoc assumptions when working around known shortcuts.
- Treat reference material as background context unless a design doc or active plan promotes it into project direction.
- When the target architecture exists before implementation files do, follow the documented target structure when adding code.
- Place new documentation by type: conceptual material in `docs/design-docs/`, active implementation plans in `docs/exec-plans/active/`, completed plans in `docs/exec-plans/completed/`, technical debt in `docs/tech-debt/`, and examples or research in `docs/references/`.
- When adding, updating, or resolving technical debt, follow `tech_debt_tracker_guide.md` and keep entries concrete, code-linked, and remediation-oriented.

## ExecPlans

For complex features or significant refactors, write an ExecPlan following `PLANS.md`, from design through implementation. Keep the ExecPlan in `docs/exec-plans/active/` while in progress and move it to `docs/exec-plans/completed/` when done.
