# ReelForge Version History Index

> **Continuous Version History Registry**
> Each major milestone, minor version, and layer in ReelForge history is documented in its dedicated file under `.reelforge/versions/`.
> Gaps or unrecorded intermediate prototypes (such as V2.0–V2.2) are explicitly acknowledged rather than fabricated.

---

## Version Registry

| Version | Title | Date | Status | Git Reference | History Document |
|---|---|---|---|---|---|
| **V1.0** | Initial Foundation & Pipeline | 2026-07-28 to 2026-08-23 | Verified | Commits `7cc85d1`..`220e540` | [`versions/V1.0.md`](versions/V1.0.md) |
| **V2.0–V2.2** | *(Intermediate prototypes)* | August 2026 | Unknown / Uncommitted | No distinct tags in git | *Acknowledged gap (see V1/V2.3)* |
| **V2.3** | OmniRoute Gateway & Multimodal Vision | 2026-09-03 | Verified | Commit `f5906f9`, tag `v2.3.0` | [`versions/V2.3.md`](versions/V2.3.md) |
| **V2.4** | State Safety & Reliability | 2026-09-04 | Verified | Commit `268afc9`, tag `v2.4.0` | [`versions/V2.4.md`](versions/V2.4.md) |
| **V2.5** | Rich OneNote Experience & Dedup | 2026-09-04 | Verified | Commit `4fda5a1`, tag `v2.5.0` | [`versions/V2.5.md`](versions/V2.5.md) |
| **V2.5.1** | Telegram Knowledge Discovery | 2026-09-04 | Verified | Commit `85d6445`, tag `v2.5.1` | [`versions/V2.5.1.md`](versions/V2.5.1.md) |
| **V2.6** | Knowledge Lifecycle & Cross-Reel Linking | 2026-09-04 | Verified | Commits `aa56157`, `77e1640`, tags `v2.6.0`, `v2.6.1` | [`versions/V2.6.md`](versions/V2.6.md) |
| **V2.7** | Grounded Evaluation System & Migration | 2026-09-04 | Verified | Commit `af43757`, tag `v2.7.0` | [`versions/V2.7.md`](versions/V2.7.md) |
| **V3.1** | MongoDB Atlas Cloud Storage & User Layer | 2026-09-10 | Verified | Commit `68a5084` | [`versions/V3.1.md`](versions/V3.1.md) |
| **V3.2** | Multi-User Brain Object Ownership | 2026-09-11 | Verified | Commit `7241185` | [`versions/V3.2.md`](versions/V3.2.md) |
| **V3.2.1** | MongoDB Ownership Index Hotfix | 2026-09-13 | Verified | Commit `87739d9` | [`versions/V3.2.1.md`](versions/V3.2.1.md) |
| **V3.3** | Per-User Microsoft OAuth & OneNote Writer | 2026-09-13 to 2026-09-14 | Verified | Commits `87739d9`, `d8bebfe` | [`versions/V3.3.md`](versions/V3.3.md) |
| **V3.4** | Cloud Portability & Runtime Independence | 2026-09-14 | Verified | Commit `d500583` | [`versions/V3.4.md`](versions/V3.4.md) |
| **V3.5** | Durable Job Queue & Worker Reliability | 2026-09-14 | Verified | Commit `d500583` | [`versions/V3.5.md`](versions/V3.5.md) |
| **V3.6.1** | Storage Lifecycle Audit & Operations Doc | 2026-09-14 | Verified | Commit `d500583` | [`versions/V3.6.1.md`](versions/V3.6.1.md) |
| **V3.6.2** | Worker Workspace Isolation | 2026-09-15 | Verified | Commit `d500583` | [`versions/V3.6.2.md`](versions/V3.6.2.md) |

---

## Maintenance Guidelines

When an agent begins working on a new version or layer:
1. Create `.reelforge/versions/V<new_version>.md`.
2. Add an entry to this table in `VERSION_INDEX.md`.
3. Keep the file updated as debugging discoveries and implementation progress occur.
