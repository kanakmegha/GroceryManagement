# MCP Capability Matrix — GroceryManagement-AgentBenchmark

**Phase:** 0 (Agent & MCP Reconnaissance)  
**Recorded At:** 2026-11-17T14:25:30+05:30

---

## 1. Connected & Verified MCP Servers

All 7 servers below are listed in `<mcp_servers>`, inherited by specialist subagents (verified on `product-researcher`, `product-architect`, `ai-ml-engineer`), and **tested via live `call_mcp_tool` invocations** in Phase 0.

| MCP Server | Installed & Connected? | Available Tools (Count / Key Examples) | Agent Access | Phase 0 Test Invocation & Result | Project Purpose |
|---|---|---|---|---|---|
| **Neon** (`neon`) | **Yes (Connected)** | ~100+ tools (`list_projects`, `create_project`, `create_branch`, `run_sql`, `prepare_database_migration`, `complete_database_migration`, `get_connection_string`, `describe_table_schema`) | Coordinator + Subagents | `neon:list_projects(limit=5)` -> **PASS** (returned 5 existing projects in `org-winter-cell-89948324`, PG 17/18). | **Authoritative PostgreSQL provider** (if hosted DB is selected); schema inspection, migrations, branching. |
| **Docker** (`docker`) | **Yes (Connected)** | 41 tools (`docker_version`, `docker_system_info`, `docker_list_containers`, `docker_compose_up`, `docker_compose_build`, `docker_exec`, `docker_container_logs`) | Coordinator + Subagents | `docker:docker_version()` -> **PASS** (Docker Desktop 4.40.0, Engine 28.0.4, API 1.48, Linuxkit). | Local containerized PostgreSQL, FastAPI backend, OCR worker, and test stack (bypasses sandboxed CLI socket block). |
| **GitHub** (`github`) | **Yes (Connected)** | 25 read tools (`get_me`, `get_file_contents`, `search_code`, `list_commits`, `list_branches`, `list_issues`, `list_pull_requests`) | Coordinator + Subagents | `github:get_me()` -> **PASS** (authenticated as `kanakmegha`, id `94185763`). | Read-only repo/commit/PR/issue inspection if a GitHub remote is linked. |
| **Context7** (`context7_context7`) | **Yes (Connected)** | 2 tools (`resolve-library-id`, `query-docs`) | Coordinator + Subagents | `context7_context7:resolve-library-id(libraryName="FastAPI", ...)` -> **PASS** (resolved `/websites/fastapi_tiangolo`, 4160 snippets). | Up-to-date official documentation lookup for FastAPI, SQLAlchemy 2.0, Expo, React Native, TanStack Query, Pydantic v2. |
| **Parallel Search** (`parallel_parallel-search`) | **Yes (Connected)** | 3 tools (`web_search`, `web_fetch`, `send_feedback`) | Coordinator + Subagents | `parallel_parallel-search:web_search(...)` -> **PASS** (`search_6db80adb706f72ceef109981f46c56e1`, returned relevant FastAPI/SQLAlchemy/asyncpg articles). | Technical research, library comparison, and documentation retrieval. |
| **Clerk** (`clerk`) | **Yes (Connected)** | 35 tools (`whoami`, `list_applications`, `create_application`, `list_users`, `list_organizations`, `clerk_sdk_snippet`) | Coordinator + Subagents | `clerk:whoami()` -> **PASS** (`user_3AEkkF1KOl2IFumM6HFUXQ2O4et`, workspace `org_3AEkkD3i3Lh1FVjpP5vxBL6A9NW`). | Available if managed auth is ever needed, though Phase 1/2 should evaluate lightweight self-contained JWT auth in FastAPI first. |
| **Playwright** (`playwright`) | **Yes (Connected)** | 22 tools (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_take_screenshot`, `browser_tabs`, `browser_network_requests`) | Coordinator + Subagents | `playwright:browser_tabs(action="list")` -> **PASS** (returned tab 0 `about:blank`). | Automated E2E web/Expo-Web UI verification and FastAPI Swagger/OpenAPI browser checks. |

---

## 2. Installed / Unauthenticated MCP Servers (Not Connected)

Reported by `list_plugin_accounts` as waiting on user sign-in (and therefore **not** available in `<mcp_servers>`):

| Candidate Integration | Status | Notes |
|---|---|---|
| **Supabase** (`supabase_mcp`) | Installed, **Not Signed In** | Requires user sign-in. **Decision:** Do NOT connect; we will use **Neon** (or local Docker PostgreSQL) as the single authoritative PostgreSQL provider. |
| **Postman** (`postman_postman`) | Installed, **Not Signed In** | Requires user sign-in. Can use FastAPI OpenAPI + `pytest`/`httpx` for API contract testing unless user chooses to sign in. |
| **MongoDB Atlas** (`mongodb-atlas_mongodb-atlas`) | Installed, **Not Signed In** | Not needed (project requires PostgreSQL). |
| **Vercel** (`vercel-plugin_vercel`) | Installed, **Not Signed In** | Not needed for local/mobile-first phases. |
| **Semrush** (`semrush_mcp`) | Installed, **Not Signed In** | Not relevant to grocery mobile app. |
| **Stripe** (`stripe_stripe`) | Installed, **Not Signed In** | Not relevant (no payments/grocery platform integrations). |
| **Chrome DevTools** (`chrome_devtools`) | Directory present on disk, **Not in `<mcp_servers>`** | Covered by `playwright` MCP if browser inspection is needed. |

---

## 3. Authoritative PostgreSQL Provider Decision

- **Provider Choice**: **Neon** (`neon` MCP) for hosted PostgreSQL **OR** **Docker PostgreSQL** (`docker` MCP) for local containerized PostgreSQL — **never** mixing Neon and Supabase.
- We will confirm hosted Neon vs. local Docker PostgreSQL in Phase 1/2 based on whether the user prefers cloud branching (`neon`) or zero-network local containers (`docker`).
