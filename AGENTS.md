# AGENTS.md

## Project

OneCode is a Python code agent runtime.

Use this file only for instructions that coding agents need while working in this repository. Runtime architecture, module design, implementation plans, technical debt, and reference material belong in the project documentation.

The source of truth for the target architecture is `architecture.md`.

## Documentation

Before changing code, read only the documentation relevant to the task:

- Architecture and dependency direction: [`architecture.md`](architecture.md)
- Core design principles: [`docs/design-docs/core-beliefs.md`](docs/design-docs/core-beliefs.md)
- Module design: [`docs/design-docs/`](docs/design-docs/)
- Active implementation plans: [`docs/exec-plans/active/`](docs/exec-plans/active/)
- Technical debt: [`docs/tech-debt/tech-debt-tracker.md`](docs/tech-debt/tech-debt-tracker.md)
- Reference material: [`docs/references/`](docs/references/)
- ExecPlan requirements: [`PLANS.md`](PLANS.md)
- Technical debt authoring rules: [`tech_debt_tracker_guide.md`](tech_debt_tracker_guide.md)

Follow existing architecture, design docs, active plans, and technical-debt decisions. Do not silently override them.

When documents disagree, use this precedence:

1. `architecture.md`
2. relevant design docs
3. active ExecPlans
4. technical-debt decisions
5. reference material

Completed ExecPlans are historical context, not current implementation direction.

When the documented target architecture exists before the implementation does, new code should follow the documented target rather than reproducing the current shortcut.

## Architecture Boundaries

These constraints are architectural invariants and are checked by `tests/test_import_boundaries.py`:

- `core/loop.py` must not import concrete tool directories or concrete providers.
- `services/tools/` must not statically import top-level `tools/<tool_name>/`.
- `tools/` may depend on `services.tools` public types and `ToolRuntime`, but not on `core/loop.py`.
- `infrastructure/` must not depend on `core/`.
- `prompts/` may read tool descriptor prompt text but must not execute tools.
- A `guard` deny must not be overridable by hooks, session allow, permission prompts, or model requests.

Before adding a capability, decide which existing extension point owns it: tool, hook, prompt section, compaction layer, transition, model/provider adapter, UI, or another documented module boundary.

Do not add capability-specific branches to the main loop when an existing extension point can own the behavior.

## Environment

OneCode uses `uv`.

```bash
# Sync development dependencies
uv sync --dev

# Activate the virtual environment on Windows
.\.venv\Scripts\Activate.ps1
```

Copy `.env.example` to `.env` for local model-provider configuration.

Model-provider environment variables are read from `.env`.

## Validation

Use the narrowest validation that gives confidence in the change.

```bash
# Targeted test
uv run python -m pytest tests/<file>.py -q

# Dependency-boundary tests
uv run python -m pytest tests/test_import_boundaries.py -q

# Compile checks
uv run python -m compileall core services infrastructure

# Full test suite
uv run python -m pytest tests -q
```

Run dependency-boundary tests after changing imports, module ownership, extension points, or package structure.

Run targeted tests while developing. Run the full suite for broad refactors, cross-module behavior changes, shared runtime changes, or before completing a significant ExecPlan.

Do not replace meaningful validation with manual inspection when an automated test can cover the behavior.

Tests should exercise public production interfaces where practical. Avoid coupling tests to private worker state, internal task objects, or implementation details unless the test specifically protects an internal invariant.

## Working Rules

- Keep `AGENTS.md` concise and repository-wide. Do not add feature-specific implementation instructions here.
- Do not duplicate architecture or design documents in this file.
- Prefer locality: behavior should live with the module that owns its state and invariants.
- Preserve dependency direction when refactoring; do not solve local problems by introducing reverse dependencies.
- Prefer existing extension points over new orchestration layers.
- Keep infrastructure and UI concerns out of the core runtime unless explicitly required by the architecture.
- Do not silently introduce compatibility layers, duplicate execution paths, or temporary abstractions without documenting why they are necessary and how they will be removed.
- Preserve structured domain data across layers; do not reduce structured messages or runtime facts to strings merely for convenience.
- When changing lifecycle or concurrency behavior, make ownership, cancellation, terminal states, and resource cleanup explicit.
- When modifying persistent formats or recovery behavior, consider backward readability and interrupted-state recovery.
- Do not overwrite unrelated user changes in the working tree.

## Technical Debt

Before changing code near a known shortcut, read:

[`docs/tech-debt/tech-debt-tracker.md`](docs/tech-debt/tech-debt-tracker.md)

When adding, changing, or resolving technical debt, follow:

[`tech_debt_tracker_guide.md`](tech_debt_tracker_guide.md)

Technical-debt entries should be concrete, linked to code, describe the accepted risk, and state the intended remediation direction.

Do not create undocumented temporary architecture as a substitute for recording known debt.

## ExecPlans

For complex features, significant refactors, lifecycle changes, persistence changes, or work spanning multiple modules, use an ExecPlan following [`PLANS.md`](PLANS.md).

Keep active plans under:

```text
docs/exec-plans/active/
```

Move completed plans to:

```text
docs/exec-plans/completed/
```

Before implementing related behavior, check whether an active ExecPlan already exists and keep the implementation aligned with it.

ExecPlans should record decisions, implementation progress, discoveries, validation evidence, and deviations from the original plan as the work proceeds.

Do not use `AGENTS.md` as an execution plan or task tracker.

## Documentation Placement

Place new documentation according to its purpose:

```text
architecture.md                  repository-wide target architecture
docs/design-docs/                conceptual and module-level design
docs/exec-plans/active/          active implementation plans
docs/exec-plans/completed/       completed historical plans
docs/tech-debt/                  accepted technical debt
docs/references/                 examples, research, and supporting context
```

Reference material does not become project direction merely by existing under `docs/references/`; promote important decisions into architecture, design docs, ADR-like design records, or active plans as appropriate.