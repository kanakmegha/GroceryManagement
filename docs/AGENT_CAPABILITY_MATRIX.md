# Agent Capability Matrix — GroceryManagement-AgentBenchmark

**Phase:** 0 (Agent & MCP Reconnaissance)  
**Recorded At:** 2026-11-17T14:25:30+05:30  
**Main Coordinating Agent:** Antigravity Coordinator (`b7627d1e-bf7b-404c-8d86-2bd55bc9429a`, Gemini 4 Hydrogen (Medium))

---

## 1. Specialist Agent Roster & Verification Status

All 9 intended specialist roles are registered in Antigravity's `<subagents>` registry and are invokable via `invoke_subagent` (which launches each subagent in its own isolated background conversation context and delivers results asynchronously via system messages).

| # | Intended Role | Exact `TypeName` | Available? | `subagent: true` / Invokable | Parallel Execution | Tested in Phase 0? | Inherits MCP Access? | Observed Native Tools | Observed Write / Command Permissions |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Product Manager | `product-manager` | Yes | Yes (`invoke_subagent`) | Supported | Not yet invoked in Phase 0 | Pending verification | Pending Phase 1 invocation | Pending Phase 1 invocation |
| 2 | Product Researcher | `product-researcher` | Yes | Yes (`invoke_subagent`) | **Verified** (concurrent with #3, #6) | **Yes** (`2a6e79a1-3120-4431-870f-c5993e3de394`) | **Yes** (all 7 connected MCP servers via `call_mcp_tool`) | `read_file`, `write_file`, `edit_file`, `search_web`, `send_message`, `schedule`, `manage_task`, `ask_question`, `call_mcp_tool`, `list_resources`, `read_resource` | `write_file`/`edit_file` declared (withheld per Phase 0); **No `run_command`**; no `list_dir`/`find_by_name`/`grep_search` |
| 3 | Product Architect | `product-architect` | Yes | Yes (`invoke_subagent`) | **Verified** (concurrent with #2, #6) | **Yes** (`cd3eabf3-00a0-455a-97f2-080dc9358f80`) | **Yes** (all 7 connected MCP servers via `call_mcp_tool`) | `read_file`, `write_file`, `edit_file`, `search_web`, `send_message`, `schedule`, `ask_question`, `manage_task`, `call_mcp_tool`, `list_resources`, `read_resource` | `write_file`/`edit_file` declared (withheld per Phase 0); **No `run_command`**; no `list_dir`/`find_by_name`/`grep_search` |
| 4 | Backend Engineer | `backend-engineer` | Yes | Yes (`invoke_subagent`) | Supported | Not yet invoked in Phase 0 | Pending verification | Pending Phase 2 invocation | Pending Phase 2 invocation |
| 5 | Frontend Engineer | `frontend-engineer` | Yes | Yes (`invoke_subagent`) | Supported | Not yet invoked in Phase 0 | Pending verification | Pending Phase 2 invocation | Pending Phase 2 invocation |
| 6 | AI/ML Engineer | `ai-ml-engineer` | Yes | Yes (`invoke_subagent`) | **Verified** (concurrent with #2, #3) | **Yes** (`d0b96f80-61f7-4287-bc57-5888aa6e56ed`) | **Yes** (all 7 connected MCP servers via `call_mcp_tool`) | `read_file`, `write_file`, `edit_file`, `run_command`, `manage_task`, `search_web`, `send_message`, `schedule`, `ask_question`, `call_mcp_tool`, `list_resources`, `read_resource` | `write_file`/`edit_file` declared (withheld per Phase 0); **`run_command` verified working** (`ls -la` exited 0) |
| 7 | QA Engineer | `qa-engineer` | Yes | Yes (`invoke_subagent`) | Supported | Not yet invoked in Phase 0 | Pending verification | Pending Phase 2/3 invocation | Pending Phase 2/3 invocation |
| 8 | Security Reviewer | `security-reviewer` | Yes | Yes (`invoke_subagent`) | Supported | Not yet invoked in Phase 0 | Pending verification | Pending Phase 6 (or earlier) invocation | Pending invocation |
| 9 | DevOps Engineer | `devops-engineer` | Yes | Yes (`invoke_subagent`) | Supported | Not yet invoked in Phase 0 | Pending verification | Pending Phase 2/6 invocation | Pending invocation |

### Additional Built-in / Domain Subagents Available (Not in Primary 9)
`self`, `research`, `browser`, `image-generator`, `firestore-rules-author`, `flutter_a11y_agent`, `ai-architect`, `deployment-expert`, `performance-optimizer`.

---

## 2. Phase 0 Delegation Experiment Evidence

Three specialist agents were invoked **concurrently** in a single coordinator turn:

1. **`product-researcher`** — Role: `Receipt OCR Researcher`
   - `conversationId`: `2a6e79a1-3120-4431-870f-c5993e3de394`
   - Dispatched: `2026-11-17T14:13:59+05:30` | Completed: `2026-11-17T08:45:07Z` (`14:15:07+05:30`)
   - Log: `/Users/skanakmegha/.gemini/antigravity/brain/2a6e79a1-3120-4431-870f-c5993e3de394/.system_generated/logs/transcript.jsonl`
2. **`product-architect`** — Role: `System & Data Architect`
   - `conversationId`: `cd3eabf3-00a0-455a-97f2-080dc9358f80`
   - Dispatched: `2026-11-17T14:14:01+05:30` | Completed: `2026-11-17T08:44:47Z` (`14:14:47+05:30`)
   - Log: `/Users/skanakmegha/.gemini/antigravity/brain/cd3eabf3-00a0-455a-97f2-080dc9358f80/.system_generated/logs/transcript.jsonl`
3. **`ai-ml-engineer`** — Role: `OCR & Normalization Evaluator`
   - `conversationId`: `d0b96f80-61f7-4287-bc57-5888aa6e56ed`
   - Dispatched: `2026-11-17T14:14:02+05:30` | Completed: `2026-11-17T08:44:47Z` (`14:14:47+05:30`)
   - Log: `/Users/skanakmegha/.gemini/antigravity/brain/d0b96f80-61f7-4287-bc57-5888aa6e56ed/.system_generated/logs/transcript.jsonl`

All three executed genuinely in parallel and returned independent outputs within ~45–68 seconds.

---

## 3. Coordinator Cross-Review & Disagreements Identified

1. **Primary OCR Strategy for Camera/Scanned Receipts (`product-researcher` vs `ai-ml-engineer`)**:
   - `product-researcher` recommends an **OCR-Anchored Vision-LLM** approach (running `PaddleOCR`/Document AI for raw tokens + bounding boxes and passing those alongside the preprocessed image to a Vision-LLM) to prevent digit hallucinations on faded thermal paper.
   - `ai-ml-engineer` recommends a **Vision-LLM-first** primary extractor (with lightweight OpenCV/EXIF preprocessing) and keeping `PaddleOCR` only as an offline/fallback or bounding-box cross-checker when math reconciliation fails, citing simpler latency/ops.
   - *Coordinator Resolution for Phase 1*: Evaluate cost/latency vs. digit accuracy during Phase 1/3; both agree on **PyMuPDF/pdfplumber text-layer gate first for digital PDFs**, **deterministic math reconciliation (`<= ₹1–2`)**, and **HITL draft staging**.

2. **Database Extensions & Schema Granularity (`product-architect` vs `ai-ml-engineer`)**:
   - `ai-ml-engineer` proposes PostgreSQL **`pg_trgm` + `pgvector` (HNSW)** for alias caching and semantic ingredient retrieval (`canonical_ingredient_id`).
   - `product-architect` proposes standard relational `canonical_items` + `purchase_lines` without yet specifying `pg_trgm`/`pgvector` extensions or an explicit `item_aliases` table.
   - *Coordinator Resolution for Phase 1*: Reconcile in Phase 1 DB design (verify `pg_trgm` / `pgvector` availability on Neon PostgreSQL 18 vs. keeping normalization aliases relational).

3. **Currency & Quantity Representation (`product-architect` vs `ai-ml-engineer`)**:
   - `product-architect` enforces **integer INR paise (`BIGINT`)** and **base metric units (`g`, `ml`, `pcs` as `NUMERIC(14,3)`)** across all tables.
   - `ai-ml-engineer`'s extraction JSON schema uses decimal INR floats (e.g. `<= 1.00`) and mixed pack units (`g | kg | ml | l | pcs | pack`).
   - *Coordinator Resolution for Phase 1*: Keep extraction boundary tolerant (`kg`/`l` + decimal INR) and deterministically convert to integer paise and base units (`g`/`ml`/`pcs`) in the backend staging layer before persistence.

4. **Tool Permission Asymmetry**:
   - `product-researcher` and `product-architect` do **not** have `run_command`, whereas `ai-ml-engineer` **does**. Implementation/test tasks must be routed to engineering subagents (`backend-engineer`, `frontend-engineer`, `ai-ml-engineer`, `qa-engineer`, `devops-engineer`).
