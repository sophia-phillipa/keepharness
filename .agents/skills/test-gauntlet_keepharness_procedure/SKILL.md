---
name: test-gauntlet_keepharness_procedure
description: Test and fix KeepHarness with an evidence-based gauntlet loop, at least seven simulated profiles and JEV-based prioritization. Use for functional and UI/UX validation and project regressions, especially with test_keepharness_engineer.
---

# Test gauntlet for KeepHarness

## Expected outcome

Run a test campaign within the requested scope: simulate at least seven distinct profiles, reproduce defects, fix their causes and repeat the affected scenarios until the selected matrix passes or there is an explicit blocker. Read `AGENTS.md` and `dossier/canonical-agents-skills-model.md` from the project root. This skill is the `test_keepharness_engineer`'s procedure; it does not change its model, effort or permissions.

## Enhanced execution prompt

> Act as KeepHarness's test engineer, specializing in Clean Code, SOLID, refactoring, UI/UX, harnesses and agentic technologies. Within the given scope, build a reproducible scenario matrix for at least seven profiles, from a beginner with no technical familiarity to specialists in software engineering and UI/UX. Use deterministic tools to gather evidence and the JEV to prioritize explicit alternatives, respecting Maestro's local priority. Run the gauntlet loop: test, reproduce, prioritize, fix, retest and review regressions. Fix the bugs found within the authorized scope, preserving permissions, data and pre-existing changes. Deliver results per profile, failure and fix evidence, commands actually run and limitations. Do not confuse simulation with research on real people, fixtures with real integration, or absence of evidence with approval.

## Initial matrix: seven required profiles

Simulate the behaviors below; do not label people as incompetent and do not spin up an agent per persona. Record familiarity, goal, input, steps, expected result and observable evidence. For each profile, include a main path and an error/recovery variation relevant to the feature. Adapt the tasks to the scope without inventing functionality. Do not repeat the same test under seven different names.

| ID | Profile | Behavior and test focus |
|---|---|---|
| P1 | Beginner with no technical familiarity | Follows only the visible text; starts a task, interprets controls and recovers from a wrong choice without knowing about CLI, session or engine. |
| P2 | Rushed user | Submits twice, switches selection quickly, cancels or goes back; checks for duplication, busy state and draft preservation. |
| P3 | Non-technical domain professional | Works with files and long conversations; needs clear results, consistent names and continuity when reopening. |
| P4 | User relying on accessibility | Operates via keyboard; checks focus, accessible names, state announcements and contrast. Does not use color as the only signal. An accessible tree does not prove a test with a real screen reader. |
| P5 | Mobile user on an unstable network | Narrow screen, reload and connection loss; checks responsiveness, reconnection, recovery and absence of content loss. |
| P6 | Software and harness engineer | Exercises contracts, permissions, isolation, model/provider switching, sessions, tools, streaming and errors; evaluates Clean Code and SOLID without speculative abstractions. |
| P7 | UI/UX specialist | Checks discoverability, plain language, consistency across screens, visual hierarchy, feedback, empty/error states and model/provider identity. |

Always keep seven profiles with scenarios in scope. If a behavior does not apply, pick another one relevant to the same profile and justify it. Do not mark an unexecuted scenario as passed. In purely internal work, tie the profiles to the contract's observable consequences; honestly record when there is no UI to evaluate.

## Gauntlet loop

1. **Prepare and measure the baseline.** Confirm the checkout, pre-existing changes and temporary test state. Use the Graphify AST and targeted searches to locate code, contracts and tests. Read current sources and the specs of the adapters involved, correlating revision and installed version. Define the matrix before fixing anything, with stable IDs like `P2-S1`. Reuse suitable tests; `tests/persona-ux-eval.spec.cjs`, `tests/gauntlet-senior-ux.spec.cjs` and `tests/gauntlet-provider-flow.spec.cjs` are starting points, not a list to run automatically.
2. **Execute.** Use real browser actions for visual scenarios and API/contract tests for internal behaviors. For each scenario, record passed, failed, blocked or not run. Collect commands, results, file/line and relevant visual evidence. Do not use tests that merely confirm your own implementation when the behavior can be observed directly.
3. **Reproduce and prioritize.** Reduce each failure to a reproducible case. Classify by impact and assign a bug ID. Data loss, permission violations and flow blockers take deterministic precedence. When there are valid alternatives with no obvious order, use the JEV as described below; a choice does not waive the other required tests.
4. **Fix.** First write a failing regression test; apply the smallest fix at the root cause; confirm it passes; refactor only when necessary. Review affected callers and contracts. Preserve other people's work. A request to test/fix authorizes fixes related to the scope, not a broad rewrite, deployment or changes to real data. Forward out-of-scope bugs to Maestro; they stay recorded and do not disappear from the report.
5. **Retest.** Run the case that used to fail and the directly affected contracts. If a regression appears, go back to step 3. After finishing the fixes, rerun the final seven-profile matrix against the same code state, also checking the browser presentation when applicable.
6. **Close with evidence.** Approve only if every required scenario in the final matrix passed and no known bugs remain in it. If access, a tool, the environment or a contract prevents proof, declare it incomplete/blocked along with the next step. After two cycles with no progress on the same failure, reassess the hypothesis with Maestro instead of repeating commands indefinitely. Do not declare approval just to close a loop or because time/budget ran out.

A gauntlet is the repetition of this matrix with fixes and regressions, not a round of opinions. Do not run the entire automated suite during development: follow the per-feature testing rule; the full suite is reserved for authorized Git milestones coordinated by Maestro. Do not create a milestone just to trigger it. A broad campaign can cover the seven profiles through selected flows without running the whole suite as a matter of routine.

## JEV: priority, not certification

Use the JEV to rank scenarios, investigations or fixes among **2-64 explicit alternatives**, prepared by Maestro from deterministic evidence. With a single valid option or an already-resolved priority, proceed directly and record the waiver. Preserve the local-model priority for approved interpretive micro-cases; do not delegate preparing the JEV's alternatives to another LLM.

- Before querying, announce: "I will use the JEV now for [purpose]"; for a batch, state the count. Review the concrete request, its path, purpose and data categories. Do not include secrets.
- Read the JEV flow documentation under the TypeSafe-JEV project, when available. Write a private temporary JSON file (0600), up to 64KiB: `{"state": minimal_evidence, "instructions": choice_criterion, "criteria": {"id_a": "alternative A", "id_b": "alternative B"}}`. `state` must relate IDs to file/line, scenario and observed result. `abstain` is reserved, not a candidate.
- Run once: `timeout 15s <typesafe-jev-venv>/bin/python <typesafe-jev-path>/jev.py decide /absolute/path/request.json --min-confidence 0.60`.
- Check the chosen ID and confidence, not just `status=ok`. With confidence >=0.75, verify the evidence before following the choice. Between 0.60 and 0.75, use it only as a reversible priority suggestion. Below 0.60, an abstention, an error or an unavailable tool: report the failure and proceed by deterministic/local priority, with no retry. Action approval remains subject to permissions; do not work around rejections.
- Record the returned usage, latency and rework; do not claim savings without measuring them. Keep requests while a review is pending and discard them once resolved. The JEV does not approve the product nor replace tests, review or acceptance criteria.

## Evidence and limits

Use synthetic data and temporary state. Simulating the profiles is not equivalent to validation with real users. Separately identify fixtures, real CLI execution without inference, browser and a real account. Tests of agentic tools verify calls, arguments, cancellation, permissions and observable effects; the model's own text claiming it executed something is not proof.

Do not trigger real paid inference, GPU benchmarks, production restarts, additional access grants or Git milestones through this skill. Authorized JEV queries for prioritization follow their own specific contract. If additional real proof is needed, report the gap to Maestro for coordination; do not change permissions or assume equivalence between providers. Do not load secrets, weights or private configuration without a testing need.

Deliver a compact report with:

- Scope, code state and environment tested.
- Matrix: profile/scenario, expected, observed, state and evidence.
- Bugs: ID, severity, reproduction, cause, changed files and proof of failure -> fix -> retest.
- Gauntlet rounds and regressions found; JEV decisions with confidence, metrics and the alternative used when relevant.
- Commands run, final results, blocked/not-run scenarios and limitations of the conclusion.

Creating this skill does not run a test campaign. When asked to test/fix, run the cycle; when asked only to edit the skill itself, validate the document and its association, without starting the product's gauntlet.
