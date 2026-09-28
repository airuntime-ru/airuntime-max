"""Persistent multi-agent orchestration engine.

The only chat-turn path (backend/src/api/routers/chat.py's `_orchestration_event_source` always
uses it). Supersedes and replaces the older `agent/orchestrator.py` in-memory decomposition
experiment (`enable_agent_orchestrator`) and the `agent/product_pipeline.py` staged pipeline it
fed into - both deleted, along with their own on/off flags.

Module map:
  schemas.py            - Pydantic contracts: ExecutionPlan, TaskContract, TaskResult, TaskEvidence, ...
  status.py             - run/plan/task status vocab + legal transition tables
  repository.py         - thin db.query()-based persistence for the 6 orchestration tables
  context_engine.py     - global/task/tool context assembly, budgeting, redaction
  role_policy.py         - SpecialistRole registry + server-enforced policy matrix
  planner.py              - LLM plan generation + structural (DAG) validation
  contract_builder.py     - PlannedTask -> TaskContract + completeness scoring
  capability_provider.py  - CapabilityProvider protocol + Platform/Skill/Mcp implementations
  capability_router.py    - task -> skill | mcp | specialist_agent | user_input | fail
  skills/                 - Skill protocol + concrete skills
  mcp/                    - MCP server registry, client, fake server for tests
  evidence.py              - factual TaskEvidence collection (independent of agent claims)
  validation.py            - scope/static/build/product/preview/runtime validation levels
  failure_policy.py        - failure classification + retry/repair/replan/rollback decisions
  replanner.py              - builds new plan versions from failures, preserves accepted work
  review_handoff.py         - QA verdict → implementer TODO list (1-2 review rounds, no Ralph loop)
  workspace_isolation.py    - DB-backed leases + git worktree isolation
  git_transaction.py        - base_sha -> lease -> execute -> evidence -> validate -> commit/rollback
  executors.py               - AgentExecutor protocol + Codex/HTTP/deterministic/skill/mcp executors
  cancellation.py             - CancellationToken + Codex-container-kill wiring
  budget.py                   - credit/token/time/attempt budgets
  events_bus.py                - durable RunEvent log + live SSE fan-out
  engine.py                    - OrchestrationEngine: the top-level run driver
"""
