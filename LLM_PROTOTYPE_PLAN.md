# Plan: an LLM-assisted operator for the HI-imaging pipeline

Status: DRAFT v0.1 (2026-09-27). Nothing here is built. v0.1 adds the didactic layer (§8),
decision-trace graphs (§4.5), the handling of unstructured data (§4.4), candidate technologies (§4.6)
and a current-status estimate (§15). Companion to `llm_eval/incidents.yaml` (the evaluation set and
operator preferences this plan depends on).

**Scope note.** The evaluation data and knowledge in this plan are cluster- and container-specific
(Setonix, the deployed `idianext.sif`, the CASA/casampi versions in it, real job IDs and scratch paths)
and exist solely for this prototype. They are not general pipeline documentation and are not expected
to transfer to other clusters or container builds without re-capturing evidence.

## 1. Goal

Turn what is currently "the user operates the pipeline through a chat session" into a
small, testable system that can:

1. **Answer** questions about the pipeline, its failures and the tools under it, with citations.
2. **Diagnose** a failed or suspicious run from real evidence (logs, `sacct`, configs, FITS headers).
3. **Propose and, with confirmation, perform** safe actions (resubmit, restore state, edit a config).
4. **Teach**: explain, on request or as it goes, the interferometry and imaging reasoning behind each
   decision, so the user learns why the pipeline does what it does (§8).
5. Run first on a **hosted** LLM, and move to a **self-hosted** one when an evaluation says it is good
   enough for the parts it is asked to do.

### Non-goals (for now)

- Unattended operation. Every write action needs a human confirmation until the evaluation shows
  otherwise, and some (delete data, overwrite a config) always will.
- Replacing the pipeline's own logic. The LLM layer calls the pipeline; it does not become part of
  `processMeerKAT/` or its CASA-free/CASA split.
- Fine-tuning. Retrieval, tools and an eval set come first; training is only worth discussing if a
  self-hosted model fails the eval for reasons retrieval can't fix.

## 2. Principles

These come from how the pipeline has actually failed and been fixed (see `incidents.yaml`).

- **Evidence over recall.** Most wrong diagnoses so far were confident readings of the wrong number
  (`chans=10` taken as the cube size, minor-cycle lines counted as major cycles, a recalled velocity
  presented as a lookup). The operator must measure, and must say whether a figure was measured,
  derived or recalled.
- **Exit status is not truth.** A `FAILED` job wrote the image; a `COMPLETED` partition wrote an
  empty MMS; SIP exited 0 without figures. Tools return facts about outputs, not just job states.
- **The user decides strategy.** Threshold/niter/masking, reliability, which combo next: the operator
  lays out options with numbers and waits.
- **Guardrails live in code, not prose.** The `.config.tmp` / `myconfig.txt` traps and "never re-run
  full `submit_pipeline.sh` mid-chain" are currently paragraphs in `CLAUDE.md`. A model reading a
  paragraph can miss it; a tool that refuses cannot.
- **Provenance and staleness on every fact.** Pipeline knowledge expires (`nmajor` default changed on
  2026-09-25; the container may be rebuilt). Each retrieved fact carries a source, a date and a status.
- **Record why, not just what.** Operating decisions are stored as traces (what was seen, which options
  were weighed, who chose, what happened), because the reasoning is what gets reused and taught (§4.5).
- **Teaching is grounded and separate.** Explanations use the user's own data and cited sources, are
  opt-in, and never delay or change an action (§8).
- **Model-agnostic from day one.** All model calls go through one adapter so the hosted-to-self-hosted
  move is a configuration change plus an eval run, not a rewrite.

## 3. Architecture

```
                      ┌──────────────────────────────────────────┐
   user (CLI / chat)  │  agent loop  (prompting, tool dispatch,  │
   ─────────────────► │  confirmation gate, citation check)      │
                      └───────┬───────────────┬──────────────────┘
                              │               │
                ┌─────────────▼───┐     ┌─────▼──────────────────┐
                │  LLM adapter    │     │  tool layer (typed)    │
                │  hosted │ local │     │  read-only → guarded   │
                └─────────────────┘     └─────┬──────────────────┘
                                              │ calls
        ┌─────────────────────────────┐   ┌───▼─────────────────────┐
        │ knowledge layer             │   │ pipeline + Slurm +      │
        │  • doc index (vector+BM25)  │   │ run directories         │
        │  • knowledge graph          │   │ (unchanged)             │
        │  • decision-trace graph     │   └─────────────────────────┘
        │  • concept graph (teaching) │
        │  • link/source registry     │
        └─────────────────────────────┘
                              ▲
        ┌─────────────────────┴───────────────┐
        │ evaluation harness (incidents.yaml, │
        │ held-out cases, regression runs)    │
        └─────────────────────────────────────┘
```

Proposed layout (new, CASA-free, outside `processMeerKAT/`):

```
llm_ops/
  adapter/        model-provider interface + hosted and local implementations
  knowledge/      ingestion, index, graph, decision traces, source registry
  teaching/       concept graph, explanations, teaching-mode prompts, learner record
  tools/          typed pipeline tools and their guardrails
  agent/          loop, prompts, confirmation gate, citation check
llm_eval/         (exists) incident cases, preferences, later: runner and scores
```

Where it runs: a login node on Setonix, because that is where the run directories, `sacct` and the
containers are. This needs outbound HTTPS to the hosted API from there (**to confirm**, see §13).

## 4. Knowledge layer

### 4.1 Internal sources (already exist)

| Source | Holds | Chunking |
|---|---|---|
| `CLAUDE.md`, `REFACTOR_PLAN.md`, `profiling_notes.md` | curated design and incident knowledge | by heading; keep the heading path |
| `git log` (533 commits) | why each change was made; several are full root-cause write-ups | one chunk per commit; store hash and date |
| `llm_eval/incidents.yaml` | causal cases (symptom → cause → action) | one chunk per case, kept structured |
| `script_registry.py`, `correlator_modes.py`, `default_config.txt` | machine-readable facts about scripts, modes, config keys | extracted to graph nodes, not embedded as prose |
| run logs (`logs/*.casa`, `.err`, `sacct`) | raw evidence | **not** bulk-indexed; matched by signature and read on demand by tools |
| session transcripts | diagnoses and user corrections | mined into incidents/preferences, then not indexed raw |

### 4.2 External documentation — the link requirement

The RAG answer must cite documentation for CASA and the other tools, with a working link. To do that
reliably, links are managed as data, not left to the model's memory.

**A source registry (`llm_ops/knowledge/sources.yaml`)**, one entry per documentation source:

```yaml
- id: casa-tclean
  title: CASA tclean task
  tool: casa
  version: "<CASA version in the container>"   # pinned, see below
  url: <canonical page>
  fetch: web | container-help | container-source | pdf
  domain_allowlist: [casadocs.readthedocs.io]
  retrieved_at: <date>
  content_hash: <sha256>
  license_note: <how it may be stored/quoted>
```

Every indexed chunk carries `source_id`, `url` (with an anchor where the page has one), `version`,
`retrieved_at`. The agent may only cite a URL that exists in the registry or that a tool has just
fetched and checked. A citation check in the agent loop rejects any link that isn't one of those.

**Candidate sources.** URLs below are the ones already used in this project or well-known
canonical locations; every one must be checked (resolves, right version) when the registry is built:

| Tool | What to index | Notes |
|---|---|---|
| CASA | task and parameter docs (`tclean`, `mstransform`, `uvcontsub`, `virtualconcat`, `flagdata`, ...), CASA Docs `casadocs.readthedocs.io` | version-specific; pin to the container's CASA |
| CASA in the container | `help(<task>)` output and the installed `task_*.py` sources | **version-exact and offline**; this is what diagnosed the `tclean`/`MPIInterface` hang and the `virtualconcat` bug |
| casampi | source and README (`github.com/casangi/casampi`, version 0.5.9 in the deployed image vs 0.6.0 override) | matters for the MPI incidents |
| SoFiA-2 | User Manual (`gitlab.com/SoFiA-Admin/SoFiA-2/-/wikis/documents/SoFiA-2_User_Manual.pdf`) and wiki | PDF, section-chunked; parameter names are the retrieval key |
| sofia-image-pipeline (SIP) | README and CLI help, `pip show` version | 1.4.0 in use |
| IDIA processMeerKAT | `idia-pipelines.github.io/docs/processMeerKAT/` | upstream behaviour; the fork has diverged, so tag as "upstream" |
| Kitchi's IDIA_pipeline | `github.com/Kitchi/IDIA_pipeline` | comparison source; already reviewed |
| Slurm | `slurm.schedmd.com` command and option pages (`sbatch`, `srun`, `sacct`, dependencies) | Setonix's Slurm version may lag or lead the web docs |
| Pawsey / Setonix | Pawsey user documentation (partitions, `--mem` per core, exclusive-node rules, `singularity` modules) | site-specific; the shared-partition memory model and the `-mpi` vs `-nohost` module behaviour live here |
| Singularity/Apptainer | `singularity exec --env`, binds, module flavours | the `LD_LIBRARY_PATH` and `--env` replace-not-compose finding |
| astropy, astroquery, pvextractor, PyBDSF, Pillow | API pages for the calls the pipeline makes | pulled on demand, low priority |
| MeerKAT | correlator-mode and channelisation documents behind `correlator_modes.py` | whatever the user considers authoritative |

**Three fetch modes**, in order of preference: `container-help` / `container-source` (exact and
offline), a pinned snapshot of a web page or PDF (versioned and hashed), and a **live fetch** tool for
when the index may be stale (returns the page plus its URL, retrieval date and hash).

**Link hygiene.** A scheduled check that every registry URL still resolves and that its content hash
hasn't changed; changed pages are re-ingested and flagged. Answers show the retrieval date next to
each link.

### 4.3 Knowledge graph

Start with the schema from the earlier discussion, using data that already exists.

- **Entities:** track, MS/MMS, correlator mode, cube; script, config key, stage, combo; job, node, run
  attempt; failure signature, root cause, fix, decision; **document** (a source-registry entry).
- **Relations:** `script reads config-key`, `job ran script`, `run used config`, `stage produces
  artifact`; `signature → cause → fix`; `config-value causes symptom`; `fix verified-in run`;
  **`claim documented-in document#anchor`** — the edge that ties a fact to a citable link.
- **Node attributes:** `status` (verified live | inferred | open), `first_seen`, `last_verified`,
  `source`, `superseded_by`.
- **Population:** structured facts (`script_registry`, `correlator_modes`, config schema) are
  extracted by code; incident triples come from `incidents.yaml`; free-text extraction by an LLM is
  proposed, then **reviewed by the user** before it enters the graph.
- **Store:** start with a graph library plus SQLite; move to a graph database only if queries need it.

Retrieval combines three lookups: text search plus embeddings over documents, graph traversal from a
matched failure signature (symptom → causes → checks → fixes), and the doc links attached to each node.

### 4.4 Handling unstructured data

The graph is not limited to structured records. Each kind of source has its own route in:

| Kind | Route | Human step |
|---|---|---|
| Structured (script registry, correlator modes, config schema, `sacct`, FITS headers) | extracted by code into nodes and edges | none beyond tests |
| Semi-structured, curated (`incidents.yaml`) | a person has already turned prose into schema-shaped cases; loaded directly | the authoring review |
| Free text (notes, commit messages, documentation, transcripts) | an LLM proposes entities and relations per chunk, each with a pointer to the exact source passage | **the user reviews before it enters the graph**; unreviewed candidates stay in a staging area and are not used for answers |
| Logs | signature matching and counting (`PMI_Init returned 1`, `iters=0->0`), with a template-mining step to discover new signatures; no LLM over raw logs | new signatures are confirmed by the user before they are linked to causes |
| Documentation pages and PDFs | chunked with heading paths and stored as text, with a `documented-in` edge from the facts they support | link check (§4.2) |

The graph always keeps the source text alongside: traversal finds candidate causes and checks, and
the linked passages supply the detail and the citation. The graph never replaces the text.

### 4.5 Decision-trace graphs

"Context graph" is used for more than one thing; two meanings are relevant here.

1. **Decision traces:** a record of why a decision was made: what was observed, which options were
   considered, who chose, and what followed. This is the part a documentation-only RAG cannot supply,
   and the project already has it in embryo: `hypothesis_trail`, `human_correction` and `preferences`
   in `incidents.yaml`.
2. **Temporal memory:** facts carry validity times ("true from A until superseded by B"), which is the
   staleness problem (`nmajor` was -1 until 2026-09-25). The `last_verified` and `superseded_by`
   attributes in §4.3 are a manual version of this.

Plan: adopt both ideas in the existing graph rather than adding a separate product.

- **Nodes:** `Observation` (a measured fact with its source), `Hypothesis` (with outcome: confirmed |
  refuted | untested), `Option` considered, `Decision` (who: user | operator; when; parameters chosen),
  `Outcome` (what happened, with the job/run reference), `Correction` (where the user redirected).
- **Edges:** `observed-in`, `led-to-hypothesis`, `tested-by`, `refuted-by`, `chose`, `resulted-in`,
  `corrected-by`, `applies-to` (a decision to a script, config key or stage), `explained-by` (a decision
  to concept nodes in §8).
- **Uses:**
  - retrieval of similar past decisions ("last time stage 0 diverged at robust 0.0, this was tried,
    this was chosen, this was the result");
  - a check that a proposed action doesn't repeat a refuted hypothesis;
  - raw material for teaching: a real decision with its real numbers (§8);
  - the source of new eval cases, so every real incident becomes a regression case.
- **Capture:** the agent loop writes a trace entry whenever the user confirms or overrides an action;
  the user can amend it. Seeded from the 46 cases and the transcript-mined corrections.

### 4.6 Candidate technologies

Candidates to evaluate against the eval set, not commitments:

- **Hybrid retrieval:** keyword search (BM25) plus embeddings, then a reranker. Exact parameter names
  (`nmajor`, `reliability.threshold`) need keyword matching. Probably the cheapest large gain.
- **Contextual chunking:** prefix each chunk with its heading path and source before embedding, so a
  paragraph from a CASA page still carries the task it belongs to.
- **GraphRAG-style methods** (entity graphs plus community summaries from text): could help with
  thematic questions across many documents; likely heavier than needed for this corpus size.
- **Temporal or agent-memory graph libraries:** for validity times and traces (§4.5); evaluate against a
  plain graph plus SQLite before adopting.
- **Structured output and tool calling:** diagnoses and actions returned as checkable JSON; constrained
  decoding on the self-hosted side.
- **A tool protocol (MCP):** expose the typed tools once and use them from any front end or model.
- **Log-template mining** for signature discovery.
- **Evaluation and tracing tooling:** an eval framework and an observability layer that records every
  retrieval and tool call. More important than the choice of vector store.
- **Vector store:** any lightweight one is enough at this scale; choose on convenience.
- **Open embedding and reranking models** for the self-hosted path, so retrieval doesn't depend on a
  hosted service.

## 5. Tool layer

Typed functions with structured results. The agent has no shell.

**Tier 0 — read-only (build first)**
`queue_status`, `job_history(job_ids)`, `job_log_signatures(job_id)` (matches known signatures such as
`PMI_Init returned 1`, `iters=0->0`, `Possible divergence`, and counts them instead of dumping logs),
`config_diff(a, b)`, `runtime_state()` (`combo`/`stage`/`continue`, diffed against `myconfig.txt`),
`fits_header(path)`, `image_stats(path, channels)`, `catalogue_summary(path)` (source count,
reliability, pixel-size outliers), `list_outputs(dir)`.

**Tier 1 — guarded writes (confirmation required)**
`resubmit_step(script, exclude_nodes)`, `set_runtime_state(combo, stage, continue)`,
`edit_config(key, value, target)` (always writes a backup and shows the diff first),
`cancel_jobs(ids)`.

**Tier 2 — destructive or irreversible (confirm every time, show exactly what is affected)**
delete products, overwrite a config, regenerate scripts with `-R`.

**Invariants enforced in the tool code**, taken from `CLAUDE.md` and the incidents: no full
`submit_pipeline.sh` mid-chain; `-R` blocked when `.config.tmp` has progressed unless state is
restored; a resubmit checks `continue=True` and `combo`/`stage`; a config edit never touches a path
marked off-limits by the user; nothing is deleted without an explicit list.

## 6. LLM adapter: hosted now, self-hosted later

One interface: `complete(messages, tools, system, temperature, max_tokens) → text | tool_calls`,
plus streaming and token accounting. Tool schemas and prompts are written once, in a provider-neutral
form, and translated by the adapter.

**Hosted (phase 1).** Anthropic API with tool use. Use a stronger model for diagnosis and planning and
a smaller, cheaper one for routine work (log-signature triage, summarising status). Use prompt caching
for the large static context (system prompt, guardrails, registry index). Exact model IDs and prices
are looked up at build time, not fixed here.

**Self-hosted (phase 4).** An OpenAI-compatible endpoint served by an open-source inference server
on Setonix GPU nodes (container), behind the same adapter. Selection is by the eval set (§9), not by
benchmark rank.

**Data leaving Setonix (hosted phase).** Only text the tools return: log excerpts, config values,
file headers, catalogue rows. No visibility or image data. A redaction step strips usernames,
absolute home paths and email addresses. Decide with the user which logs/configs may be sent, and
whether the project's data policy allows a hosted API at all (§13).

## 7. Agent loop

1. Retrieve context (documents, graph neighbours) for the user's request.
2. Ask the model for a plan; tool calls run through the tool layer.
3. For any Tier 1 or 2 action, show exactly what will happen and wait for the user's yes.
4. Before answering, run the **citation check** (every link is registry-backed) and the
   **provenance check** (each figure labelled measured, derived or recalled).
5. Prefer "I don't know, here is what I'd check" over an unsupported diagnosis.

The user's stated preferences (`llm_eval/incidents.yaml`, `preferences:`) become a checked-in system
prompt and, where possible, tool-level rules.

## 8. Didactic layer

Goal: as the user operates the pipeline, they learn the interferometry and imaging reasoning behind
each choice, not just the choice.

**Why it fits.** Every decision point in this pipeline has a physical reason the project has already
worked out: `robust` and the synthesized beam, the cell-size rule (18–22 pixels per beam area),
w-projection and `wprojplanes`, major versus minor cycles and `nmajor`, threshold versus noise,
`auto-multithresh`, SoFiA's channel-unit kernels, reliability as a positive-versus-negative count.

**Design**

- **Concept graph.** Nodes for concepts (uv-coverage, visibility weighting, the synthesized beam and
  PSF, w-projection, CLEAN major/minor cycles, noise and thresholds, masking, matched filtering,
  reliability, channel width and velocity resolution, ...), with prerequisite edges. Each node has a
  short explanation and links to cited sources (standard textbooks and synthesis-imaging course
  material chosen by the user, plus the tool documentation from §4.2).
- **Decision points link to concepts.** A `Decision` (§4.5) or a config key points at the concepts it
  depends on, so "why is stage 0's threshold 0.6 mJy?" leads to noise, thresholds and divergence.
- **Worked examples from the user's own data,** not textbook numbers: the actual beam
  (11.6″ × 6.9″), pixels per beam before and after rebinning (22.7 and 5.7), the measured noise
  (~0.28 mJy/beam), the real divergence counts. Traces from real incidents are the examples.
- **Teaching mode is opt-in** (off by default; per-session or per-decision). Levels:
  1. *Why:* one or two sentences at each decision, with the numbers.
  2. *Predict, then reveal:* ask what the user expects (for example the residual after stage 0) before
     showing it.
  3. *Deep dive:* the concept chain with prerequisites and citations, on request.
- **Learner record (minimal).** Which concepts have been shown or answered, so explanations don't
  repeat and prerequisites are offered when needed. Stored per user, viewable and deletable.
- **Separation from operation.** Teaching output is generated after or alongside an action, never in
  the path of a confirmation, and never changes what an action does.

**Content and quality**

- The bottleneck is curated content, not code: level, sequencing and correctness of the physics need
  the user's review. Expect this to be slower than the engineering.
- Teaching answers are held to the same citation rule as everything else: every physical claim has a
  registry-backed source, or is labelled as the operator's own reasoning.
- Unsupported physics is worse than an unsupported log reading, because it gets learned. The
  evaluation (§9) checks explanations against their cited sources, not just tone.

**What it can and can't do.** It can make each decision legible and connect it to the user's own data.
It is not a curriculum, and it doesn't replace reading the textbook; it points to the relevant
sections. Whether it actually teaches (as opposed to feeling helpful) has to be measured, for example
by whether the user's predictions improve over time; the plan doesn't assume it.

## 9. Evaluation

Built first, because it is the only way to compare hosted models, retrieval settings and the future
self-hosted model on equal terms.

- **Data:** the 46 cases and 13 preferences in `incidents.yaml`, split into a **development** set and
  a **held-out** set. Held-out cases must not appear in `CLAUDE.md`/`profiling_notes.md` before they
  are used, otherwise retrieval just finds the answer. New incidents go into held-out first.
- **Replay:** each case is run with its real evidence where a log exists (`raw_logs`) and needs a
  captured excerpt where it doesn't. 41 of the 46 currently need one.
- **Metrics:**
  - diagnosis correct (against `root_cause` and the rubric)
  - action safe and correct (against `action` and `wrong_actions`)
  - repeated a known wrong hypothesis (from `hypothesis_trail`)
  - **citation validity** (link resolves and supports the claim) and **fabricated-link rate**
  - abstention when evidence is missing; correct use of "transient, resubmit"
  - provenance labelling of figures
  - **explanations (§8):** correct against the cited source, no unsupported physical claims, and
    appropriate to the level asked for (reviewed by the user)
  - **decision-trace retrieval (§4.5):** does it surface the relevant past decision, and does it avoid
    repeating a refuted hypothesis
  - cost and latency
- **Baselines to beat:** (a) model alone, (b) model + document retrieval, (c) + graph, (d) + tools.
  Each step should earn its place on the held-out set.
- **Grading:** rubric checks by a model judge, spot-checked by the user, with disagreements reviewed.
- **Regression:** every real incident from now on is added as a case, and a run happens before any
  prompt, retrieval or model change.

## 10. Phases

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0. Foundations** (small) | eval runner over `incidents.yaml`; captured log excerpts for as many cases as possible; held-out split | can score any model/prompt on the cases; baseline (a) measured |
| **1. Knowledge + retrieval** (medium) | source registry with checked links; ingestion of internal docs and CASA/SoFiA/Slurm/Pawsey sources, including container `help()`/source; hybrid search; citations | baseline (b) beats (a) on the held-out set; **fabricated-link rate ≈ 0** |
| **1b. Didactic layer** (medium; content-limited) | concept graph seeded with the ~15 concepts behind the pipeline's decisions, cited sources, teaching mode at the "why" and "predict then reveal" levels, minimal learner record | explanations pass the correctness check against their sources; the user finds the "why" notes correct and useful on real decisions |
| **2. Read-only tools** (medium) | Tier 0 tools, signature matcher, chat/CLI loop | it reproduces the diagnoses in the eval from real evidence, without writes |
| **3. Graph + traces + guarded writes** (medium–large) | knowledge graph with provenance; decision-trace capture; Tier 1/2 tools with invariants and confirmation gate | baseline (c)/(d) measured; traces retrievable and used by the eval; no guardrail bypass in adversarial tests; the user runs a real resubmit through it |
| **4. Self-hosted trial** (large) | serving container on Setonix GPU nodes; adapter for it; side-by-side eval | per-task decision: which tasks can move (e.g. log triage) and which stay hosted; documented gaps |
| **5. Operate + improve** (ongoing) | new incidents → cases → regression; graph reviewed monthly | eval scores stable or rising as the pipeline changes |

Sizes are relative (small = days, medium = a couple of weeks, large = a month or more of part-time
work); revise after Phase 0.

## 11. Transition to a self-hosted model

Move task by task, not all at once.

1. Run the same eval on the candidate open model with the same retrieval and tools.
2. Compare per task class: log-signature triage and Q&A over docs are likely to move first; multi-hop
   diagnosis and planning are the likely holdouts.
3. **Router:** cheap local model first, escalate to the hosted model when confidence is low or the case
   is in a hard class; log every escalation to find what the local model can't do.
4. Requirements to check before starting: context length needed for a log excerpt plus retrieved
   documents; tool-calling reliability; GPU memory and queue time on Setonix; the container/`--nv`
   (or ROCm) setup for the site's GPUs; who owns and updates the serving stack.
5. Only if the local model falls short in ways retrieval and prompting can't fix, consider
   adaptation using the reviewed incident set — and keep the held-out set out of any training data.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Confident wrong diagnosis (already the main historical failure) | evidence-first tools, provenance labels, "measure before proposing", held-out eval |
| Hallucinated or stale links | registry-only citations, link checker, retrieval dates, live fetch |
| Documentation version mismatch (CASA docs vs the container's CASA) | pin to the container version; prefer container `help()`/source |
| An agent damages data or state | Tier gating, backups before edits, invariants in code, no shell |
| Knowledge goes stale (defaults change, container rebuilt) | status/`last_verified`/`superseded_by` on every fact; update on each pipeline change |
| Eval contamination (answers in the indexed docs) | held-out cases kept out of the index until used |
| Sending sensitive text to a hosted API | redaction, excerpts only, an explicit data policy, self-hosted path |
| Small eval set (46 cases, one author, one pipeline) | keep adding real incidents; treat results as directional early on |
| Wrong physics in teaching answers | citation required for every claim; correctness check against the source; user review of the concept set |
| Teaching that feels helpful but doesn't teach | measure it (prediction accuracy over time); keep it opt-in and cheap to switch off |
| Decision traces recorded wrongly or incompletely | the user can amend a trace; traces reference the run and job they came from |
| Over-building before the eval says it's needed | each phase must beat the previous baseline on held-out cases |

## 13. Open decisions for the user

1. **Data policy:** may pipeline log excerpts and configs go to a hosted API? Any projects or
   collaborators' data with restrictions? (Determines redaction and how soon a local model is needed.)
2. **Where the agent runs:** Setonix login node (files are local; needs outbound HTTPS) vs elsewhere
   with a mounted or synced view of the runs. Not yet checked whether the login node can reach the
   hosted API.
3. **Interface:** CLI first, or keep Claude Code as the front end and add the tools as an MCP server,
   which would be the fastest path to Phase 2.
4. **Scope of write access** in Phase 3: which Tier 1 actions you would trust to run after one
   confirmation, and which must always show a full diff.
5. **Authoritative documents:** which MeerKAT and site documents count as ground truth; whether the
   Pawsey documentation may be stored locally.
6. **Reviewer:** the eval, the graph and the concept set all need your review; how much time per week
   is realistic.
7. **Didactic scope:** the intended learner (yourself, students, collaborators) and their starting
   level; which textbooks or course notes count as the cited sources; whether teaching mode should
   default off.
8. **Trace capture:** whether every confirm/override should be recorded automatically, or only ones
   you mark as worth keeping.

## 14. Suggested first steps

1. Write the eval runner and capture log excerpts for the cases that lack them (Phase 0).
2. Draft `sources.yaml` for CASA, SoFiA-2, SIP, Slurm, Pawsey and Singularity, and run the link check.
3. Check that `help(<task>)` and `task_*.py` can be read from the container for the tasks in use.
4. Decide §13 items 1–3 before any hosted call is made with real logs.
5. List the concepts behind the decisions in the current pipeline, and pick the sources they will cite
   (input to Phase 1b).

## 15. Where we are now (estimate, 2026-09-27)

A judgement, not a measurement. By effort, roughly 15–20% of the build is done; by hard-to-replace
content it is much more, because the domain knowledge is the slow part to acquire.

| Component | State |
|---|---|
| Domain knowledge | most of the hard part exists: curated docs, incident write-ups, 46 cases, 13 preferences |
| Evaluation | cases written; no runner, no held-out split; 41 of 46 lack a replayable log (~30%) |
| Knowledge layer | raw material only; no source registry, index or graph (~10%) |
| Decision traces | seeds exist (`hypothesis_trail`, `human_correction`, preferences); no schema or capture (~10%) |
| Tools and guardrails | guardrails documented in prose; no typed tools yet. Two standalone finishing tools exist as templates (`hi_postprocess.py`, `hi_sip.py`, with `--dry-run`/`--check` and refuse-on-bad-state behaviour) |
| Agent loop | the user and Claude Code currently act as the loop: useful as reference behaviour, not embedded |
| Didactic layer | not started; the concepts and the worked-example numbers exist in the notes |
| Model adapter and self-hosted path | not started |

Largest unknowns, none yet tested: whether the login node can reach the hosted API, whether logs may
leave the cluster, whether a self-hosted model can do the multi-step diagnosis, and how far a
46-case set from one author can be trusted.
