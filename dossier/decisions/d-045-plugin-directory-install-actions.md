# D-045 — Plugin directory, details and explicit installation actions

Status: accepted for W6 #25. Date: 2026-10-08. Decided under Sophia's task authorization. Builds on D-020, D-021, D-022, D-038, D-039, D-041 and D-042; supersedes #25's old D-034 allow-list wording. Parent: #14. Installation follow-up: W7 #27.

## Decision

Extend the existing Plugins page with a directory grouped by marketplace, using collapsible sections and two-column cards (one column on narrow screens). Include available catalog plugins and snapshot-only installed plugins. Keep search, Refresh, notices and the rail, Settings and composer entry points. The directory is the default; Manage opens the existing installed list, and Browse directory returns. A card's install affordance opens its detail so the person can choose a provider explicitly.

The family key associates providers for display, after reconciling catalog and snapshot records by exact provider and item ID. Different display names for that same identity cannot create another card or hide its installed state, even when the catalog still says available. A family offered by multiple marketplaces remains visible in every contributing marketplace section; it does not disappear into whichever provider was read first. Multiple installation identities for the same provider require an explanation rather than an arbitrary write target.

Opening a card shows a Plugins breadcrumb, icon, title, description, known Version, Apps, Skills, developer and source information when supplied by the catalog. Missing metadata is not invented. Omit unbacked hero prompts, Try now, Copy link, connected accounts and Personal tabs. Use KeepHarness theme tokens, keyboard-operable controls and readable light/dark text.

Metadata belongs to the variant in the originating marketplace; do not combine one provider's version with another provider's developer or source. The catalog exposes bounded public strings for marketplace, version, developer and source, plus bounded app/skill name lists. Source URLs are sanitized on the server: only public HTTP(S) URLs, without userinfo, query or fragment, can reach the API response or detail. File URLs and local paths are omitted. Raw manifests, credentials, author email and local source paths are not detail metadata. Long titles and list metadata must wrap within a 390px detail viewport. The installed Manage list continues matching snapshot items by exact ID; directory family association must not change its write targets.

Source authorities require valid ASCII/punycode DNS labels or a global IP address. Encoded or malformed hosts, local numeric aliases and scoped IP addresses are omitted; valid explicit ports are preserved. A sanitized source longer than 500 characters is omitted rather than truncated into a different URL. The correction-round JEV query about IDNA handling was blocked by the session's approval policy; the conservative local fallback accepts ASCII/punycode and omits unsupported Unicode spellings.

The Providers block distinguishes each provider independently:

| Evidence | Control |
| --- | --- |
| Snapshot contains an installed, writable item | Enabled switch writing that provider's item ID, deciding scope and fingerprint through the existing provider-state route |
| Installed item is not writable | Disabled switch with the adapter's reason |
| Snapshot confirms absence and that provider's catalog offers an executable installation identity | Explicit Install action using the existing provider installation route |
| Provider supports installation but no facade route exists | Disabled Install action with the reason; implementation belongs to W7 #27 |
| No supported installation identity/path | Explanation; no Install action and no absent-item switch |
| Provider state cannot be read | Explain the read failure; do not infer absence or expose an installation action |
| Catalog reports installed but snapshot has no matching item | Explain unavailable installed state; do not infer absence or offer another installation |

Installation, enablement, Manage and Uninstall remain separate actions. A family match is a display association, never authorization to reuse another provider's identifier for a write. Existing installed-only management and uninstall behavior stays available. No KeepHarness integrations allow list returns.

## Existing installation path and W7 boundary

`control/operations.py::operation` already accepts `plugin_install`, mapping Codex to `plugin add` and Claude to `plugin install`. The existing `integration` route and admin operation machinery are reused; this task adds no CLI command or provider write route. Rendering and opening details do not install anything. W7 #27 owns genuinely missing installation paths and Create/Add/Upload actions, including their provider-specific safeguards.

## JEV and implementation choice

The JEV compared extending `customize.js` with extracting a new frontend module, both preserving the same provider contracts. It abstained (confidence 0.64, required 0.80; one call, 798 input tokens, 40 output tokens, 306 ms). Local fallback: extend the existing renderer and reuse native controls and existing routes, avoiding a new module solely for this view. The source check above established that installation routes already exist; the disabled-action rule is conditional on a missing route, not the default for Codex and Claude.

For the multi-provider card affordance, a second JEV query compared opening the detail, opening a provider menu and choosing the first provider. It selected the detail (confidence 0.89, required 0.80), keeping the installation target visible and explicit.

The JEV selected separate directory/Manage modes (confidence 0.98). The marketplace-placement query failed with `context_evaluation`; local fallback projects each family into every contributing marketplace and preserves the originating card for focus restoration. These changes retain the existing management assertions and provider write contracts.

## Verification

The following simulated-profile matrix maps the behavior to named scenarios in `tests/admin-plugin-directory.spec.cjs`. Each row includes a main path and a recovery or refusal variation; these are fixture simulations, not studies with real users.

| Profile | Main path and variation; named scenarios |
| --- | --- |
| P1 Beginner | Browse and collapse marketplaces; search and refresh: `directory-marketplace-two-column-family-grouping`, `search-metadata-and-refresh-recovery` |
| P2 Rushed user | Explicit installation writes one exact target; return to the originating card: `provider-absent-explicit-install-exact-target`, `keyboard-detail-breadcrumb-focus-return` |
| P3 Domain professional | Inspect metadata from either marketplace; unknown and hostile metadata remain omitted or text: `coherent-origin-marketplace-metadata`, `hostile-and-optional-metadata` |
| P4 Accessibility | Keyboard entry/back restores focus; light/dark text meets 4.5:1: `keyboard-detail-breadcrumb-focus-return`, `paper-and-graphite-aa-contrast` |
| P5 Mobile/network | Narrow viewport has no overflow; failed provider reads leave explanations and no writes: `mobile-directory-overflow`, `unreadable-provider-recovery` |
| P6 Engineer | Independent provider enablement and exact management IDs; mismatch never borrows a sibling's state: `provider-enabled-and-disabled-independent-switches`, `manage-exact-id-state-binding`, `provider-catalog-state-mismatch-and-unavailable-explanations` |
| P7 UI/UX | Installed read-only state has a reason; absent, unavailable/not-offered and ambiguous targets remain distinct: `provider-installed-readonly-reason`, `provider-catalog-state-mismatch-and-unavailable-explanations` |

`test_plugin_detail_metadata_is_allowlisted_and_bounded` checks catalog extraction. `test_plugin_directory_javascript_family_cases_share_the_python_contract` and the browser spec consume `tests/fixtures/plugin-directory.json`, checking the JavaScript copy against `agent_service/integrations_view.py::family_key`. Existing `admin-customize`, `admin-plugins-switches`, `admin-provider-notices`, `harness-rail-settings`, `settings-system-admin`, `settings-submenu`, `composer-plugins` and `harness-back-forward` specs cover management, notices and #26 entry points, including remote fallback and navigation history. Missing-route behavior remains a W7 requirement: both current providers already have routes, so no fictitious unsupported route is introduced just for a fixture.

The correction-round scenarios `public-source-api-and-dom-redaction`, `provider-id-before-family-reconciliation` and `mobile-detail-long-metadata` cover source sanitization before the JSON response and detail, differing catalog/snapshot names for one installed ID (including stale available entries), and long unbroken metadata in the detail at 390px. Each finding was reproduced against `bd92153` before accepting its fix.
