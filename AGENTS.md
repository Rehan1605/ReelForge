# ReelForge Coding Agent Instructions

> **CRITICAL ENTRYPOINT RULE FOR ALL CODING AGENTS**
>
> Before modifying any ReelForge code, every coding agent must read `.reelforge/PROJECT_STATE.md` and `.reelforge/ACTIVE_TASK.md`, then inspect the current Git working tree and diff.
>
> Do not assume that previous agents completed their assigned work. Verify the actual repository state.
>
> If project documentation conflicts with the working tree, preserve the code and investigate the discrepancy before making changes.

---

## Canonical Project Brain

All architectural decisions, current development state, active task recovery checkpoints, agent protocols, and version history are maintained permanently inside the `.reelforge/` directory:

- [`.reelforge/PROJECT_STATE.md`](.reelforge/PROJECT_STATE.md) — The single source of truth for repository status, completed versions, and current layer state.
- [`.reelforge/ACTIVE_TASK.md`](.reelforge/ACTIVE_TASK.md) — The active task recovery checkpoint (objectives, allowed files, executed tests, exact next steps).
- [`.reelforge/AGENT_PROTOCOL.md`](.reelforge/AGENT_PROTOCOL.md) — The mandatory operating rules, safety invariants, and Git checkpoint workflow for all agents.
- [`.reelforge/DECISIONS.md`](.reelforge/DECISIONS.md) — Persistent architectural decisions and constraints (avoids repeating past mistakes).
- [`.reelforge/ARCHITECTURE.md`](.reelforge/ARCHITECTURE.md) — Structural design, data flow, storage hierarchy, and component relationships.
- [`.reelforge/CHANGELOG.md`](.reelforge/CHANGELOG.md) — Chronological version summary with links to detailed version logs.
- [`.reelforge/VERSION_INDEX.md`](.reelforge/VERSION_INDEX.md) — Index of all version records in `.reelforge/versions/`.

## Mandatory Agent Workflow

1. **Before Coding**: Read `PROJECT_STATE.md`, `ACTIVE_TASK.md`, and relevant sections of `DECISIONS.md`. Run `git status` and `git diff` to understand partial changes.
2. **During Development**: Respect allowed files and architecture invariants. Record discoveries, debugging findings, and design rationale.
3. **After Checkpoint**: Run relevant tests, update `ACTIVE_TASK.md` and `PROJECT_STATE.md`, and report findings.
4. **Git Safety**: Never independently run `git commit` or `git push`. Present diffs and test results to the human for approval. A local commit is an approved checkpoint; push is separate.
