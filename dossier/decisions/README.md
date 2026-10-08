# Decision records

Immutable records of product and architecture decisions. A record is never edited in substance or deleted: a changed decision gets a new record that supersedes the old one, and the old record's status line only gains "superseded by D-NNN".

Format: `d-NNN-<kebab-topic>.md`, heading `# D-NNN — <title>`, a status line (status, date, who decided, links to the spec and release notes), then Context, Decision, Rationale, Alternatives, Impact and, when needed, Follow-up or Open. Ids continue after D-017; D-001 to D-017 belong to the private, unpublished redesign study and are not in this repository.

## Codex-app parity (0.16.0)

Spec: [Codex-app parity design](../codex-parity-design.md).

| Id | Decision | Status |
|---|---|---|
| [D-018](d-018-codex-parity-architecture.md) | Build parity on the existing shell, Settings dialog, admin and integrations API | accepted |
| [D-019](d-019-parity-product-answers.md) | Sophia's answers to the parity product questions | accepted |
| [D-020](d-020-theme-tokens-for-copied-screens.md) | Codex-copied screens use theme tokens, never Codex colors | accepted |
| [D-021](d-021-keep-keepharness-only-features.md) | "Copy Codex" never removes a KeepHarness-only feature | accepted |
| [D-022](d-022-wp3-copy-codex-customize.md) | WP3 copies Codex Customize without a mockup step | accepted |
| [D-023](d-023-wp6-preferences-in-backend-store.md) | WP6 keeps UI preferences in a backend store per owner | accepted |
| [D-024](d-024-wp6-limits-and-merge.md) | WP6 limits, per-key merge and older builds | accepted |
| [D-025](d-025-wp4-enable-opens-providers.md) | WP4 "Enable" opens Settings › System › Providers | accepted |
| [D-026](d-026-wp5-desktop-hand-off.md) | WP5 hand-off to ChatGPT and Claude desktop apps | accepted |
| [D-027](d-027-wp7-deterministic-visual-markers.md) | WP7 visual markers are deterministic and display-only | accepted |
| [D-028](d-028-wp8-quota-meters-every-provider.md) | WP8 quota meters for every connected provider | accepted (not started) |
| [D-029](d-029-visible-pass-before-merge.md) | Every parity WP gets a visible pass before merge | accepted |
| [D-030](d-030-wp5-host-hand-off-in-containers.md) | WP5 hand-off on the host inside distrobox | accepted |
| [D-031](d-031-retry-failed-turns.md) | Retry failed or interrupted turns, and image-capability guidance | accepted |
| [D-032](d-032-wp8-quota-meter-contract.md) | WP8 quota meter contract: passive activity, non-null quota, DeepSeek balance | accepted |
| [D-033](d-033-right-panel-accordion.md) | Right panel: Activities first, accordions, Project and System files | accepted |
| [D-034](d-034-wp3-customize-product-answers.md) | WP3 Customize: allow-list switches, provider-only Add actions, "Plugins" label, marketplace grouping | accepted |
| [D-035](d-035-rail-meters-provider-logos.md) | Rail quota meters show provider logos instead of names | accepted |
| [D-036](d-036-wp9-interface-language.md) | WP9 interface language: per-locale JSON catalogs and a DOM-attribute runtime | proposed |
| [D-037](d-037-chat-code-views-of-one-conversation.md) | Chat and Code are two views of one conversation (records ledger D45) | accepted |
| [D-038](d-038-keepharness-facade-over-provider-state.md) | KeepHarness is a facade over the providers' real state (supersedes D-034 §1 and the 0.15 provider homes) | accepted |
| [D-039](d-039-single-owner-facade-policies.md) | Single-owner facade: no guests or run classes, trust like the CLIs, `~/.claude.json` safeguards, no 0.15 migration (supersedes UC-001 multi-identity and the D03/D04/D12/D15 clamps) | accepted |
| [D-043](d-043-deepseek-engine.md) | DeepSeek 1.0 keeps the Codex engine and #47 fixes; later `dsh` adoption requires an opt-in gate | accepted for implementation |
| [D-044](d-044-scoped-sandbox-under-facade.md) | Retire Codex/Claude scoped execution for 1.0; retain native presets and Local isolation | accepted for implementation |
| [D-046](d-046-cloud-isolation-ui-retirement.md) | Bring cloud isolation UI retirement ahead of #45/#46; re-validate their checks when they land | accepted |
| [D-048](d-048-fail-closed-hook-command-masking.md) | Mask hook command values by default instead of guessing secrets (#61) | accepted |
