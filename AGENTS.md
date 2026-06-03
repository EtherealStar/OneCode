# AGENTS.md

## Purpose

OneCode is a code agent runtime project. This file is a short orientation guide for agents entering the repository. It should help agents quickly understand where project knowledge lives and how to use it before starting work.

This file is not an execution plan, architecture document, or task list. For architecture details, use `architecture.md`.

## First Reading Order

Before making project changes, read these sources in order:

1. `architecture.md`
   - The root architecture document is the source of truth for the target runtime structure, module boundaries, dependency direction, and overall flow.

2. `docs/design-docs/`
   - Read relevant design documents for core beliefs, conventions, and abstract project decisions.
   - These documents explain the reasoning behind the system, not step-by-step implementation work.

3. `docs/exec-plans/active/`
   - Always check relevant active execution plans before implementing related behavior.
   - Active plans describe work that is currently intended, in progress, or still being shaped.

4. `docs/references/`
   - Use reference material as supporting context, examples, or external notes.
   - References can inform implementation, but they do not override `architecture.md`, design docs, or active execution plans.

## Documentation Map

- `architecture.md`
  - Describes the overall target architecture for OneCode.
  - Use it to decide where responsibilities belong.

- `docs/design-docs/`
  - Holds conceptual and higher-level design documents.
  - Use it for project philosophy, conventions, and stable design reasoning.

- `docs/exec-plans/active/`
  - Holds currently active implementation plans.
  - Use it to understand the current direction before changing related code or docs.

- `docs/exec-plans/completed/`
  - Holds archived execution plans that have already been implemented.
  - Use it for historical context only.

- `docs/references/`
  - Holds reference documents, notes, images, example code, and topic-specific research material.
  - Existing reference topics include agent loops, tool use, permissions, hooks, context compaction, memory, system prompts, error recovery, and task systems.

## Working Guidance

- Keep this file concise and general. Do not add task-specific instructions here.
- Do not duplicate the contents of `architecture.md`; link agents to it instead.
- Prefer active execution plans over completed plans when judging current implementation intent.
- Treat reference material as background context unless a design doc or active plan explicitly promotes it into project direction.
- If the repository contains target architecture before implementation files exist, follow the documented target structure when adding new project code.
- When adding new documentation, place conceptual material in `docs/design-docs/`, implementation plans in `docs/exec-plans/active/`, completed plans in `docs/exec-plans/completed/`, and supporting examples or research in `docs/references/`.

