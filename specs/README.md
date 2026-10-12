# Spec-driven development

This repository uses lightweight, repository-native SDD; no external Spec Kit installation is claimed.

For each behavior change:

1. Write `specs/<number>-<name>/spec.md` first: observable requirements with stable IDs, safety boundaries and exclusions.
2. Write regression/acceptance tests for those IDs. Distinguish unit, integration, connected read-only and connected execution evidence.
3. Implement within the spec. If scope changes, revise the spec before claiming completion.
4. Record requirement-to-test mappings and actual commands/results in `verification.md`. Record blocked or deferred checks explicitly.
5. Deploy only within user authorization. Keep demo/live-money and position-change approvals separate from coding approval.

The existing roadmap describes long-term delivery; it is not an acceptance record. `002-safe-trade-clarification` is the first change managed through this explicit workflow. Earlier release tests remain regression gates, but have not been retroactively represented as a fully specified process.
