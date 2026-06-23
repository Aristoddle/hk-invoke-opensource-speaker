# Documentation Index

Read these in order when joining the project:

1. [`../README.md`](../README.md) — overview, status, quickstart.
2. [`product-brief.md`](product-brief.md) — product management brief, goals, users, metrics.
3. [`roadmap.md`](roadmap.md) — milestone roadmap with gates and acceptance criteria.
4. [`setup.md`](setup.md) — local setup, dependencies, credential policy, artifact locations.
5. [`operator-runbook.md`](operator-runbook.md) — exact hardware/operator steps for Joe.
6. [`architecture.md`](architecture.md) — system architecture, staged data flows, security posture.
7. [`backlog.md`](backlog.md) — prioritized task inventory.
8. [`current-state.md`](current-state.md) — current recovered device facts.
9. [`research-plan.md`](research-plan.md) — original phase-based research plan.

## Reference / deep-dives

- [`firmware-bringup-research-2026-06-22.md`](firmware-bringup-research-2026-06-22.md) — device truth, firmware set, Wi-Fi root-cause.
- [`opensource-speaker-architecture.md`](opensource-speaker-architecture.md) — open-source speaker build plan (audio/WiFi/BT/controls/HA).
- [`wire-a-new-invoke-runbook.md`](wire-a-new-invoke-runbook.md) — repeatable RAM-only bring-up runbook.
- [`recovery-escape-hatch-stack-2026-06-23.md`](recovery-escape-hatch-stack-2026-06-23.md) — recovery/escape-hatch ladder + NAND golden dump that makes persistence safe.
- [`persistence-and-recovery-design-2026-06-22.md`](persistence-and-recovery-design-2026-06-22.md) — durable persistence tiers (P0 host-autoboot → T1 data overlay → T2/T3/T4 gated/forbidden) ranked by brick-risk + reversibility; the bright line (mtd0–mtd5). **Canonical for the persistence mechanism.**
- [`durability-and-runs-live-design-2026-06-22.md`](durability-and-runs-live-design-2026-06-22.md) — "runs live" OPS design: host autopilot daemon, on-device supervision/health endpoint, graceful degradation ladder, remote management, HA wiring; what "runs live, better" looks like per tier. Companion to the persistence + escape-hatch docs.

## Documentation rules

- Update existing docs before adding new docs.
- Keep hardware state facts in `current-state.md`.
- Keep roadmap/priority changes in `roadmap.md` and task-level work in `backlog.md`.
- Keep operator-facing physical instructions in `operator-runbook.md`.
- Keep credentials and secrets out of every doc.
