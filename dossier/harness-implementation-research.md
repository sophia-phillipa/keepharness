# Harness implementation research — public sources

Reviewed: 2026-09-20. Status: source review of public web material. This is not an endorsement, not a provider approval, and not a verification that any recommendation fits this project.

Every URL under "Verified sources" returned HTTP 200 and was fetched with `curl` on the review date. Each line paraphrases what was actually read in the page body; no source was listed from a search snippet alone.

## Method

- Search: Brave Search HTML results, extracted locally with `curl` and shell text processing. One batch of nine queries was attempted.
- Engines rejected on evidence, same session: Bing (HTTP 200 but results unrelated to the submitted query), DuckDuckGo HTML (anti-bot page containing `anomaly`), Mojeek and Ecosia (HTTP 403), Yep (HTTP 403/Cloudflare), Qwant (HTTP 200, no result links), SearX (HTTP 200, zero result nodes).
- Rate limit: after the fifth Brave query the remaining four queries returned HTTP 429. Those sub-topics were covered by fetching canonical documentation directly.
- Coverage: 43 URLs tested, 42 returned HTTP 200.
- Verified evidence per source: HTTP status, page title, and body fragments containing the word "harness" or the specific recommendation paraphrased below.
- Numbers reported by a source (score deltas, deployment counts) are that author's own and were not reproduced here.

## Maestro-JEV record

- Request 1 (search strategy, 5 candidate engines): `status=ok`, `choice=github_api`, `confidence=0.66`, `model=jev-1.13.0`, `input_tokens=668`, `output_tokens=71`, `elapsed_ms=794`.
- Interpretation: 0.60 ≤ 0.66 < 0.75, so the choice was a reversible priority suggestion only, not a validated decision. Working local evidence (a search endpoint returning relevant results) took precedence; the GitHub suggestion was retained as a complementary source. No retry, no rework.
- Request 2 (where to store this document, 2 candidates): `status=ok`, `choice=existing_dossier`, `confidence=0.99`, `model=jev-1.13.0`, `input_tokens=582`, `output_tokens=54`, `elapsed_ms=861`.
- Both requests were prepared locally and executed through the local `jev.py` CLI. The remote endpoint `https://api.typesafe.ai` was not called in these runs.

## Verified sources

### 1. Harness concept and agent harness engineering

- https://addyosmani.com/blog/agent-harness-engineering/ — design the harness backwards from the behaviour you want; it names Anthropic's engineering writing as the best public breakdown of harness design for long-running work.
- https://martinfowler.com/articles/harness-engineering.html — harness engineering for coding-agent users; classify the desired state by *what the harness is supposed to regulate* rather than by tool.
- https://www.langchain.com/blog/the-anatomy-of-an-agent-harness — follow "behaviour we want (or want to fix) → harness design"; the filesystem is treated as a key harness primitive.
- https://www.langchain.com/blog/better-harness-a-recipe-for-harness-hill-climbing-with-evals — hill-climb harness quality with evals; cites Meta-Harness (Stanford) and Auto-Harness (DeepMind) as formalisations of the step sequence.
- https://www.databricks.com/blog/ai-harness — "Harness design directly shapes agent performance": the harness is the performance lever, not an implementation detail.
- https://www.mongodb.com/company/blog/technical/agent-harness-why-llm-is-smallest-part-of-your-agent-system — the harness comprises six components, and the LLM is the smallest part of the agent system.
- https://handbook.modular.com/model-interaction/agent-harnesses/ — the harness translates model decisions into actions and must enforce domain restrictions and confirmation rules; agent loops are among the most demanding harness workloads.
- https://www.decodingai.com/p/agentic-harness-engineering — define what a harness is before composing its parts.
- https://www.datacamp.com/tutorial/agent-harness-engineering — practical project layout, including a harness entrypoint and a project-memory file written first.
- https://en.wikipedia.org/wiki/Agent_harness — a minimal harness is unnecessary for a single prompt-and-response exchange and becomes important as tasks grow multi-step, tool-oriented, or long-running.
- https://parallel.ai/articles/what-is-an-agent-harness — the harness connects the model to the outside world (tools, memory between steps) and is distinct from orchestration and from frameworks.
- https://arxiv.org/html/2609.01437 — HarnessDev: LLMs creating and iteratively revising their own harness from downstream execution feedback.
- https://picrew.github.io/LLM-Harness/ — survey comparing prompt, context, and harness engineering as expanding scopes of system design.
- https://github.com/ai-boost/awesome-harness-engineering — curated list; stated principle: each component exists because the model cannot do it alone, and the best harnesses assume components will become unnecessary.
- https://github.com/Gloriaameng/Awesome-Agent-Harness — formalises the harness as an architectural object `H = (E, T, C, S, L, V)` and maps open challenges.
- https://nyosegawa.com/en/posts/harness-bench/ — HarnessBench: the author's first finding is that differences between coding-agent harnesses are real and measurable.
- https://llmconfigurator.com/en/guides/coding-agents/agent-harness-local-llm — production harnesses compact context in stages: trim stale tool output, then summarise older turns, then rebuild the working set.

### 2. Evaluation harnesses in practice

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents — if grading is unfair, tasks are ambiguous, valid solutions are penalized, or the harness constrains the model, the eval itself must be revised.
- https://deepeval.com/blog/what-is-an-eval-harness — an eval harness is the infrastructure that runs the evaluation (data, cases, execution, scoring), not the evaluation content.
- https://www.braintrust.dev/articles/ai-agent-evaluation-framework — step-by-step construction of an agent evaluation harness, including which metric families to combine.
- https://www.freecodecamp.org/news/how-to-evaluate-ai-agents-with-an-llm-as-a-judge-harness-in-python — worked tutorial in which the harness runs the agent and collects the answer plus tool calls per test case.
- https://towardsdatascience.com/building-an-evaluation-harness-for-production-ai-agents-a-12-metric-framework-from-100-deployments/ — frames production failure as an evaluation problem and proposes a 12-metric framework.
- https://hamel.dev/notes/llm/evals/inspect.html — review of Inspect AI written with its creator; useful as an independent read on the framework's design.

### 3. Frameworks and tooling

- https://inspect.aisi.org.uk/ — "Inspect is a framework for frontier AI evaluations developed by the UK AI Security Institute and Meridian Labs"; the docs are organised around tasks, solvers and scorers.
- https://inspect.aisi.org.uk/evals/ — Inspect Evals: reuse published eval sets instead of writing every eval from scratch.
- https://www.aisi.gov.uk/blog/inspect-evals — announcement of open-sourcing dozens of LLM evaluations to advance safety research.
- https://www.aisi.gov.uk/blog/the-inspect-sandboxing-toolkit-scalable-and-secure-ai-agent-evaluations — describes a toolkit for running agent evaluations safely; the page body is JavaScript-rendered, so only title and meta description could be verified here.
- https://github.com/EleutherAI/lm-evaluation-harness — few-shot evaluation framework; supports external tracking configuration per run.
- https://www.promptfoo.dev/docs/getting-started/ — minimal path: install, initialise an example, then iterate on a YAML file of prompts, providers and test cases with a web view for comparison.
- https://github.com/promptfoo/promptfoo — tests prompts, agents and RAGs, and also covers red teaming and vulnerability scanning.
- https://github.com/openai/evals — framework for evaluating LLMs and LLM systems plus an open registry of benchmarks.
- https://github.com/harbor-framework/terminal-bench-1 — benchmark for LLMs on complicated terminal tasks; the search engine result `/laude-institute/terminal-bench` redirects to this repository.

### 4. Benchmark harnesses for coding agents

- https://www.swebench.com/SWE-bench/reference/harness/ — Docker-based evaluation harness that builds reproducible environments, applies patches, runs tests, and decides whether a patch resolves the issue.
- https://www.swebench.com/SWE-bench/guides/evaluation/ — documents caching by `run_id`: reusing a `run_id` with a different prediction diff reuses the first run's cached results instead of re-evaluating.
- https://github.com/SWE-bench/SWE-bench — reference repository for the benchmark.
- https://github.com/Aider-AI/aider-swe-bench — the harness Aider uses to benchmark itself against SWE-bench.
- https://labs.ramp.com/swebench — Ramp's internal benchmark for measuring frontier coding agents on real company engineering tasks.

### 5. Classic test harness (software testing)

- https://developer.harness.io/docs/ai-test-automation/best-practices/creating-and-maintaining-tests-best-practices/ — strategies for designing, editing and executing reliably repeatable end-to-end tests.
- https://www.tricentis.com/learn/test-harness — a reusable harness is presented as the path to reliable, repeatable and efficient test automation.
- https://www.shiplight.ai/blog/test-harness-ai-automation — where tests are generated at machine speed, harness design decides whether the suite grows sustainably or collapses; prefer a fallback strategy over a single mode.
- https://zetcode.com/terms-testing/test-harness/ — taxonomy of harness types with their use cases and characteristics.
- https://www.headspin.io/blog/fundamentals-of-test-harness — a well-designed harness resets the required state and supplies known inputs.

## Not verified

- https://openai.com/index/introducing-swe-bench-verified/ returned HTTP 403 to two different user agents and was excluded from the findings. Its content was not read.
- Four Brave queries returned HTTP 429 and their sub-topics were not searched: promptfoo discovery, terminal-bench discovery, CI test-harness design patterns, and OpenAI/Anthropic harness blog discovery. The first two were covered by fetching documentation directly; the last two remain uncovered.
- Appeared in search results but never fetched, therefore not confirmed here: `github.com/UKGovernmentBEIS/inspect_ai`, `ukgovernmentbeis.github.io/inspect_evals`, `ukgovernmentbeis.github.io/as-evaluation-standard`, `pypi.org/project/swebench`, `swebench.com/SWE-bench/api/harness/`, `github.com/princeton-nlp/SWE-bench`.

## Consequence for this project (proposal, nothing implemented)

No code, configuration or version identifier in this repository was changed by this review; there is no new release to document.

The verified sources converge on four claims that are relevant to this project:

- Harness design dominates agent performance, including context compaction, filesystem access and confirmation rules.
- Harness quality is measured with evals, and an eval that constrains or unfairly grades the model must be revised rather than trusted.
- Sandboxing, permissions and reproducibility (containerised environments, cached results keyed by run id) are harness responsibilities, not add-ons.
- Classic test-harness practice still applies: reset state, supply known inputs, and keep the harness reusable.

## Unresolved questions

- Whether the four rate-limited sub-topics would add sources that change the four claims above.
- Whether Inspect, promptfoo or an existing eval registry should replace or complement any adapter testing here; this review did not evaluate compatibility with this codebase.
- Whether Brave Search remains rate-limited for this host on later runs.
