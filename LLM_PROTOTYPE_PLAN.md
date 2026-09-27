# Plan: an LLM-assisted operator for the HI-imaging pipeline

Status: DRAFT v0 (2026-09-27). Nothing here is built. Companion to `llm_eval/incidents.yaml`
(the evaluation set and operator preferences this plan depends on).

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
4. Run first on a **hosted** LLM, and move to a **self-hosted** one when an evaluation says it is good
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
        │  • link/source registry     │   └─────────────────────────┘
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
  knowledge/      ingestion, index, graph, source registry
  tools/          typed pipeline tools and their guardrails
  agent/          loop, prompts, confirmation gate, citation check
llm_eval/         (exists) incident cases, preferences, later: runner and scores
```

Where it runs: a login node on Setonix, because that is where the run directories, `sacct` and the
containers are. This needs outbound HTTPS to the hosted API from there (**to confirm**, see §12).

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
on Setonix GPU nodes (container), behind the same adapter. Selection is by the eval set (§8), not by
benchmark rank.

**Data leaving Setonix (hosted phase).** Only text the tools return: log excerpts, config values,
file headers, catalogue rows. No visibility or image data. A redaction step strips usernames,
absolute home paths and email addresses. Decide with the user which logs/configs may be sent, and
whether the project's data policy allows a hosted API at all (§12).

## 7. Agent loop

1. Retrieve context (documents, graph neighbours) for the user's request.
2. Ask the model for a plan; tool calls run through the tool layer.
3. For any Tier 1 or 2 action, show exactly what will happen and wait for the user's yes.
4. Before answering, run the **citation check** (every link is registry-backed) and the
   **provenance check** (each figure labelled measured, derived or recalled).
5. Prefer "I don't know, here is what I'd check" over an unsupported diagnosis.

The user's stated preferences (`llm_eval/incidents.yaml`, `preferences:`) become a checked-in system
prompt and, where possible, tool-level rules.

## 8. Evaluation

Built first, because it is the only way to compare hosted models, retrieval settings and the future
self-hosted model on equal terms.

- **Data:** the 40 cases and 11 preferences in `incidents.yaml`, split into a **development** set and
  a **held-out** set. Held-out cases must not appear in `CLAUDE.md`/`profiling_notes.md` before they
  are used, otherwise retrieval just finds the answer. New incidents go into held-out first.
- **Replay:** each case is run with its real evidence where a log exists (`raw_logs`) and needs a
  captured excerpt where it doesn't. 31 of the 40 currently need one.
- **Metrics:**
  - diagnosis correct (against `root_cause` and the rubric)
  - action safe and correct (against `action` and `wrong_actions`)
  - repeated a known wrong hypothesis (from `hypothesis_trail`)
  - **citation validity** (link resolves and supports the claim) and **fabricated-link rate**
  - abstention when evidence is missing; correct use of "transient, resubmit"
  - provenance labelling of figures
  - cost and latency
- **Baselines to beat:** (a) model alone, (b) model + document retrieval, (c) + graph, (d) + tools.
  Each step should earn its place on the held-out set.
- **Grading:** rubric checks by a model judge, spot-checked by the user, with disagreements reviewed.
- **Regression:** every real incident from now on is added as a case, and a run happens before any
  prompt, retrieval or model change.

## 9. Phases

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0. Foundations** (small) | eval runner over `incidents.yaml`; captured log excerpts for as many cases as possible; held-out split | can score any model/prompt on the cases; baseline (a) measured |
| **1. Knowledge + retrieval** (medium) | source registry with checked links; ingestion of internal docs and CASA/SoFiA/Slurm/Pawsey sources, including container `help()`/source; hybrid search; citations | baseline (b) beats (a) on the held-out set; **fabricated-link rate ≈ 0** |
| **2. Read-only tools** (medium) | Tier 0 tools, signature matcher, chat/CLI loop | it reproduces the diagnoses in the eval from real evidence, without writes |
| **3. Graph + guarded writes** (medium–large) | knowledge graph with provenance; Tier 1/2 tools with invariants and confirmation gate | baseline (c)/(d) measured; no guardrail bypass in adversarial tests; the user runs a real resubmit through it |
| **4. Self-hosted trial** (large) | serving container on Setonix GPU nodes; adapter for it; side-by-side eval | per-task decision: which tasks can move (e.g. log triage) and which stay hosted; documented gaps |
| **5. Operate + improve** (ongoing) | new incidents → cases → regression; graph reviewed monthly | eval scores stable or rising as the pipeline changes |

Sizes are relative (small = days, medium = a couple of weeks, large = a month or more of part-time
work); revise after Phase 0.

## 10. Transition to a self-hosted model

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

## 11. Risks

| Risk | Mitigation |
|---|---|
| Confident wrong diagnosis (already the main historical failure) | evidence-first tools, provenance labels, "measure before proposing", held-out eval |
| Hallucinated or stale links | registry-only citations, link checker, retrieval dates, live fetch |
| Documentation version mismatch (CASA docs vs the container's CASA) | pin to the container version; prefer container `help()`/source |
| An agent damages data or state | Tier gating, backups before edits, invariants in code, no shell |
| Knowledge goes stale (defaults change, container rebuilt) | status/`last_verified`/`superseded_by` on every fact; update on each pipeline change |
| Eval contamination (answers in the indexed docs) | held-out cases kept out of the index until used |
| Sending sensitive text to a hosted API | redaction, excerpts only, an explicit data policy, self-hosted path |
| Small eval set (40 cases, one author, one pipeline) | keep adding real incidents; treat results as directional early on |
| Over-building before the eval says it's needed | each phase must beat the previous baseline on held-out cases |

## 12. Open decisions for the user

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
6. **Reviewer:** the eval and the graph both need your review; how much time per week is realistic.

## 13. Suggested first steps

1. Write the eval runner and capture log excerpts for the cases that lack them (Phase 0).
2. Draft `sources.yaml` for CASA, SoFiA-2, SIP, Slurm, Pawsey and Singularity, and run the link check.
3. Check that `help(<task>)` and `task_*.py` can be read from the container for the tasks in use.
4. Decide §12 items 1–3 before any hosted call is made with real logs.
