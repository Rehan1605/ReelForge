# ReelForge Coding Agent Protocol

> **THE CANONICAL REELFORGE AGENT CONSTITUTION**
>
> "Before modifying any ReelForge code, every coding agent must read .reelforge/PROJECT_STATE.md and .reelforge/ACTIVE_TASK.md, then inspect the current Git working tree and diff.
>
> Do not assume that previous agents completed their assigned work. Verify the actual repository state.
>
> If project documentation conflicts with the working tree, preserve the code and investigate the discrepancy before making changes."

---

## 1. Operating Rules for Coding Agents

### Rule 1: Single Source of Truth
`.reelforge/PROJECT_STATE.md` is the authoritative record of project progress. Do not guess what version or layer the repository is on. Cross-check documentation against the actual Git working tree and test outputs before taking any action.

### Rule 2: Recovery-First Architecture
Every agent must assume the previous agent could have crashed, run out of tokens, or timed out mid-edit. Before writing or modifying any file, read `.reelforge/ACTIVE_TASK.md` to see what partial changes exist, what tests were executed, and what acceptance criteria remain.

### Rule 3: Maintain the Project Brain
The `.reelforge/` directory is not static documentation—it is a living engineering log maintained by agents. Whenever you make meaningful changes or reach a milestone, update the brain files before handing back control.

---

## 2. Before Coding Checklist

Before making ANY changes to ReelForge code:

1. **Read `PROJECT_STATE.md`**: Understand the active version, layer status, and overall project context.
2. **Read `ACTIVE_TASK.md`**: Identify the specific task, allowed files, forbidden files, and current recovery checkpoint.
3. **Read `DECISIONS.md`**: Review past architectural decisions so you never re-introduce forbidden patterns (e.g. adding object storage when media is ephemeral, or bypassing MongoDB).
4. **Inspect Git Status**: Run `git status` to see modified and untracked files.
5. **Inspect Git Diff**: Run `git diff` to understand all existing partial changes.
6. **Run Baseline Tests**: Run relevant test scripts to establish that the working tree passes prior to your edits.
7. **Verify Completeness**: Never assume previous work is complete until tests prove it.

---

## 3. During Development

1. **Stay Within Scope**: Modify only the files designated in `ACTIVE_TASK.md`. If an unexpected file modification is required, document why in `ACTIVE_TASK.md` first.
2. **Preserve Comments & Docstrings**: Maintain existing inline comments, docstrings, and architectural explanations.
3. **Record Discoveries**: If you discover a bug, edge case, or tricky environment behavior (e.g. Windows encoding, MongoDB index requirements), record it in the active task notes and version history.
4. **Preserve Incomplete Work**: If you cannot finish a task, leave explicit markers in `ACTIVE_TASK.md` explaining what is done, what is broken, and what remains.

---

## 4. After Each Meaningful Checkpoint

When an implementation step or layer is completed:

1. **Run the Full Relevant Test Suite**: Verify that the new feature passes and existing features have no regressions.
2. **Update `ACTIVE_TASK.md`**:
   - Record actual test outcomes (e.g. `59/59 passed`).
   - List files changed.
   - Note any known limitations or follow-ups.
3. **Update `PROJECT_STATE.md`**: Reflect the newly verified layer or version status.
4. **Update Version History**: Append notes to `.reelforge/versions/V<version>.md`.
5. **Update `CHANGELOG.md`**: Summarize high-level changes.
6. **Report to Human**: Present the exact changes and test outcomes clearly.

---

## 5. What an Agent Must NEVER Do

- **NEVER** assume a layer or task is complete without running verification tests.
- **NEVER** overwrite or revert unexplained uncommitted changes in the working tree.
- **NEVER** run `git reset --hard` or `git checkout -- .` without explicit user authorization.
- **NEVER** run `git commit` without explicit human approval.
- **NEVER** run `git push` without explicit human approval.
- **NEVER** modify unrelated architectural components (e.g. touching MongoDB schemas during a Telegram UI task).
- **NEVER** fabricate test results or claim tests passed without actually executing them in the shell.
- **NEVER** duplicate the entire Project Brain into temporary files or instructions.

---

## 6. If Interrupted (Crash / Disconnect Protocol)

If you are a newly initialized agent picking up an interrupted task:

1. **Inspect Git Status**: Run `git status`.
2. **Inspect Git Diff**: Run `git diff` to see what code was written.
3. **Read `ACTIVE_TASK.md`**: Look at the "Last Agent Status" and "Known Partial Changes" sections.
4. **Run Verification Tests**: See which tests currently pass and which fail.
5. **Update `ACTIVE_TASK.md`**: Record your findings and the current recovery state.
6. **Proceed Methodically**: Continue implementation only after having full situational awareness.

---

## 7. Git Checkpoint Workflow

ReelForge follows a strict, disciplined Git checkpointing workflow:

```
Agent works
    ↓
Agent runs relevant tests
    ↓
Agent updates Project Brain (.reelforge/)
    ↓
Agent reports exact changes and test outcomes
    ↓
Human reviews the work
    ↓
Human approves a local Git commit
    ↓
(Push is handled separately when appropriate)
```

### Key Principles:
1. **Commit ≠ Push**: A local Git commit is a safe recovery checkpoint that locks in verified progress. Pushing to a remote repository is a separate release action.
2. **Small Logical Checkpoints**: Avoid accumulating dozens of uncommitted files across multiple layers or versions. Create small, logical, reviewable checkpoints per layer.
3. **No Blind Commits**: Agents must never commit unfinished, broken, or unreviewed code merely to create a checkpoint.
