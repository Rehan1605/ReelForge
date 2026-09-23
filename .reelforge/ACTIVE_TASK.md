# Active Task Recovery Checkpoint

> **CRITICAL RECOVERY NOTICE FOR NEXT AGENT**:
> Read this file in full before touching any code. This checkpoint documents the exact state left by the previous session, the verified test outcomes, the committed history, and the exact remaining actions.

---

## 1. Task Metadata

- **Task Name**: ReelForge V3.6 Layer 2 — Worker Workspace Isolation & Brain Checkpoint
- **Status**: `CHECKPOINT_1_COMMITTED_PREPARING_CHECKPOINT_2`
- **Current Version**: V3.6 (Layer 2)
- **Last Commit**: `d500583` (*feat(v3): cloud portability, durable jobs, and worker workspace isolation*)
- **Primary Test File**: `scratch/test_v36_layer2_workspace.py`
- **Secondary Test File**: `scratch/test_v36_layer1_storage_audit.py`
- **Last Verification Timestamp**: 2026-09-23

---

## 2. Objective

Establish Checkpoint 2: Permanent ReelForge Project Brain (`.reelforge/` directory and `AGENTS.md` entrypoint).
Checkpoint 1 (`d500583`) successfully locked in all application and operations code across V3.4 (Cloud Portability), V3.5 (Durable Jobs & Worker Reliability), V3.6.1 (Storage Audit & Operations Specification), and V3.6.2 (Worker Workspace Isolation).

---

## 3. Scope & File Boundaries

### Staging Scope for Checkpoint 2:
- `AGENTS.md` (Root entrypoint instruction pointing agents to `.reelforge/`)
- `.reelforge/PROJECT_STATE.md` (Verified source of truth)
- `.reelforge/ACTIVE_TASK.md` (Active task recovery checkpoint)
- `.reelforge/ARCHITECTURE.md` (System architecture specification)
- `.reelforge/DECISIONS.md` (ADRs 001–009)
- `.reelforge/AGENT_PROTOCOL.md` (Agent operating constitution)
- `.reelforge/CHANGELOG.md` (Version changelog)
- `.reelforge/VERSION_INDEX.md` (Version index)
- `.reelforge/versions/*.md` (All 15 version history records)

### Application Code:
- Strictly frozen; 0 changes.

---

## 4. Current Implementation State

1. **Checkpoint 1 Committed**:
   - `d500583` on branch `main` includes all 29 files spanning V3.4–V3.6.2.
2. **Permanent Project Brain**:
   - Written, verified, and ready for Checkpoint 2 commit.
3. **Application Test Health**:
   - 59/59 workspace isolation tests pass.
   - 19/19 storage audit tests pass.
   - 244/244 V3 suite tests pass.

---

## 5. Next Steps

1. Human review and approval of Checkpoint 2 staging snapshot.
2. Commit Checkpoint 2 locally (`docs(reelforge): establish permanent project brain and agent protocol`).
3. Retain push as a separate decision.
