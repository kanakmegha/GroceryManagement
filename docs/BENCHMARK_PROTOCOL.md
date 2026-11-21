# Benchmark Protocol — GroceryManagement-AgentBenchmark

**Version:** 1.0  
**Initialized:** 2026-11-17T14:13:01+05:30

---

## 1. Objective
Measure delivery velocity, delegation effectiveness, defect/rework rates, and MCP utility when building a clean-room React Native (Expo + TypeScript) + FastAPI + PostgreSQL application using Antigravity multi-agent orchestration.

## 2. Tracked Metrics (Strictly Observed, Never Fabricated)
1. **Timestamps**: Project start, phase start/end, and total phase duration (wall-clock).
2. **Agent Invocations**:
   - Subagent `TypeName`, `Role`, `conversationId`, dispatch timestamp, completion timestamp.
   - Whether invocations ran **concurrently** (parallel batch) or sequentially.
   - Distinction between **specialist subagent work** vs. **coordinator orchestration/integration work**.
3. **MCP Tool Calls**: Server name, tool name, purpose, and pass/fail outcome.
4. **Artifacts & Code**: Exact files created/modified per phase and owning agent.
5. **Verification & Tests**: Exact commands run, scope (unit/integration/E2E), pass/fail/skip counts observed after final edits.
6. **Quality & Friction**: Integration failures, rework cycles, sandbox/infrastructure blocks, and human interventions / approval gates.

## 3. Execution & Ownership Rules
- **No concurrent edits to the same file** by multiple agents. Every parallel dispatch must specify disjoint file ownership.
- **Clean-room isolation**: Never modify, copy code from, or delete anything in the parent `GroceryManagement` workspace outside `GroceryManagement-AgentBenchmark/`.
- **Phase gates**: Each phase (0 through 6) ends with a factual summary and waits for explicit user approval before starting the next phase.
- **Truthfulness**: Only report tool outputs, test counts, and timings directly observed in this session.
