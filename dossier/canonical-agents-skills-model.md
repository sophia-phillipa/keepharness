# Canonical agent and skill model

## Mandatory rule

Every new agent and skill that belongs to this project must use **objective_context_role**, with names always in English, lowercase ASCII, no accents. Use exactly two `_` to separate the three stems; within each stem, use `-` between compound words (kebab-case). Descriptions and instructions may remain in the project's working language. There are exactly three stems, obtained by splitting the name on `_`: objective, context and role. Each stem can be compound; `keepharness` is a single entity in the context stem. The fixed context `keepharness` always occupies the second stem. Do not abbreviate it to `kh` or split it into `keep-harness`.

- **Objective:** an English verb in its base form and, when needed, an object that differentiates the specialty: `test`, `integrate-codex`, `develop-interface`.
- **Context:** always `keepharness` in this project.
- **Role:** the agent's role (`engineer`) or the skill's function (`procedure`, `guide`, `audit`). The artifact's type is also identified by its directory and format.

Agent example: `test_keepharness_engineer` = `test` + `keepharness` + `engineer`.
Example for a future skill: `validate-interface_keepharness_procedure`. This example does not create a skill.

The name contains no model, effort, version, seniority or personal name; that data belongs to configuration/description. IDs must be unique within their catalog and compatible with the consumer. Use the same English ID across every language of the interface and documentation.

## Files and contracts

Agents: `.codex/agents/<id>.toml`, with `name` equal to the file's base name. Preserve `description`, `model`, `model_reasoning_effort` and `developer_instructions` following the format already used in the project. The description and instructions must state the objective, area, limits, coordination and expected evidence.

Skills: in the skills directory recognized by the adopted runtime, use `<id>/SKILL.md` and the same ID in the frontmatter's `name` field. The description states when to use it; the body defines inputs, procedure, limits and validation. Confirm the format and discovery on the real consumer; this document does not assume portability across engines.

External skills, installed plugins and global agents belong to their distributors: do not rename them automatically. The standard applies to the project's own artifacts. There is no need to rename technical API, provider or model IDs.

## Catalog and migration

| Previous name | Canonical name |
|---|---|
| `nucleo` | `maintain-core_keepharness_engineer` |
| `modelos_mcp` | `integrate-contracts_keepharness_engineer` |
| `interface` | `develop-interface_keepharness_engineer` |
| `operacao` | `operate-runtime_keepharness_engineer` |
| `provedor_codex` | `integrate-codex_keepharness_engineer` |
| `provedor_claude` | `integrate-claude_keepharness_engineer` |
| `provedor_gemini` | `integrate-gemini_keepharness_engineer` |
| `provedor_deepseek` | `integrate-deepseek_keepharness_engineer` |
| `provedor_local` | `integrate-local_keepharness_engineer` |
| New | `test_keepharness_engineer` |

In 0.15.0 the product was renamed to KeepHarness and the context stem became `keepharness`; each developer renames the local `.codex/agents/` files and their `name` fields to match this table.

The nine existing agents keep their models, efforts and areas. The new test engineer uses `gpt-6-astra` with `low` effort (Astra Light). Its persona adopts the perspective of 30 years of development and refactoring experience, specializing in Clean Code, SOLID, UI/UX, harnesses and agentic technologies; it does not represent a real human biography.

Update file names, `name` fields, cross-references and operational documentation together. Do not keep silent old aliases. Historical release documents preserve the names used at the time; this table explains the migration. Sessions that already loaded the catalog may keep the old names: an on-disk change does not prove a reload; verify discovery in a new session before claiming runtime availability.

## Creation and review checklist

1. Define the three English stems and check whether an equivalent agent or skill already exists.
2. Read AGENTS.md, scope the responsibility, and preserve Maestro policies, permissions and per-feature tests.
3. Create the file in the consumer's format, aligning path and `name`.
4. Update this catalog for agents, references and the documentation index; record new project-owned skills with their path.
5. Validate exactly three parts via `name.split("_")`, with `keepharness` in the second position and each part matching `[a-z]+(?:-[a-z]+)*`; validate parsing, uniqueness, identity between name/path, and links; search for old operational references. Test discovery on the runtime when available and record limitations.

Agent definitions under `.codex/agents/` are local to each developer and are not versioned; `AGENTS.md` and `.agents/skills/` are. This document is the versioned reference for naming; adopting agent definitions in another checkout requires creating them locally and verifying their discovery.

## Validation of this change — 2026-09-20

Local validation with Python `tomllib` and assertions: the ten agents and `.codex/config.toml` have valid TOML; unique IDs, three semantic components, a single context, and a match between file and `name`. A search across the tracked operational Markdown files, AGENTS.md and definitions found no old provider/contract IDs. Links to this document were checked. `git diff --check` passed. No functional suite was run: the change is configuration and documentation only, with no Git milestone.

Runtime discovery was not performed: `codex` is not on the PATH for this run, and this session's catalog was loaded before the rename. Neither a reload nor an execution of the new agent is declared.

In the initial decision, before the separators were explicitly corrected, the JEV prioritized the semantic convention: `semantic`, confidence 0.87, model `jev-1.13.0`, 619 input tokens, 40 output tokens and 912ms returned. The choice was checked against the local files; no retry or rework of the choice. No savings were measured.

Naming correction: all ten IDs and files were migrated to English (`objective_context_role`), including `test_keepharness_engineer`. The intermediate non-English form was replaced across operational references. TOML validation, uniqueness, name/file matching and preservation of models/efforts were repeated; runtime discovery remains unverified.

Separator correction: the ten agents now use `objective_context_role`, with `_` between stems and `-` within compound terms. Files, `name` fields and operational references were updated together. Local validation repeated: exactly three stems, `keepharness` context, valid TOML, unique names and preserved models/efforts. Runtime discovery remains unverified.

## Project skills

| Canonical name | File | Associated agent |
|---|---|---|
| `test-gauntlet_keepharness_procedure` | [SKILL.md](../.agents/skills/test-gauntlet_keepharness_procedure/SKILL.md) | `test_keepharness_engineer` |

The same three-stem pattern applies to agents and skills. In the gauntlet skill: objective `test-gauntlet`, context `keepharness`, role `procedure`. The association is made through an explicit read instruction in the agent's TOML and in AGENTS.md; it does not assume a nonexistent skills-configuration field.

### Validation of the gauntlet skill — 2026-09-21

YAML frontmatter, the local three-stem convention, seven distinct profiles and the TOML association were checked. `agent_service.resources.discover` found the skill in the project; `skills/list` from Codex CLI 0.155.0-alpha.9.2 returned the canonical name and `enabled=true`, without starting inference. The generic `quick_validate.py` rejects underscores because it requires hyphen-case; the project's explicit rule was preserved, without changing the validator. The venv's Python did not have PyYAML; YAML validation used the already-available system Python, with no installation.

The JEV prioritized the seven-profile baseline before the fixes: `baseline`, confidence 0.97, model `jev-1.13.0`, 585 input tokens, 41 output tokens and 755ms returned; one query, no retry, no rework of the choice and no savings claimed. Creating/validating the skill did not run a product gauntlet.
