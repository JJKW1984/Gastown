# Gastown Agent Prompt

Below is the full prompt to hand off to an agent to complete the documentation work.

---

**PROMPT FOR AGENT**

You are working in repo `Gastown` (owner JJKW1984), branch `master` (default `main`). Goal: create and commit a comprehensive documentation suite.

**Context (project basics)**
- Python 3.11+, FastAPI app with multi-agent orchestration.
- Key modules: `gastown/agents/{mayor,polecat,witness,refinery}.py`, `gastown/orchestrator.py`, `gastown/storage.py`, `gastown/models.py`, `gastown/web/app.py`, `gastown/cli.py`.
- Tests: 82 passing (unit, security, performance). Benchmark info in `PERFORMANCE_REPORT.md`. Test summary in `TEST_REPORT.md`.
- Deployment: GitHub Actions workflow `.github/workflows/azure-webapp-deploy.yml` using OpenTofu; infra in `infra/terraform/*`; docs already: `docs/azure-webapp-terraform-deploy.md`, `docs/opentofu-local-dev.md`.
- Frontend: `gastown/web/static/index.html` (Tailwind dashboard).
- LLM via LiteLLM; default model `anthropic/claude-sonnet-4-6`; env vars for providers.

**What to produce (create/update files)**
1) `README.md` (root): comprehensive overview with sections:
   - What is Gastown (problem/solution), agent roles (Mayor, PoleCAT, Witness, Refinery), beads/convoys.
   - Quick start (install, env vars, CLI run, web serve).
   - Architecture overview + ASCII flow.
   - Configuration (core env vars + provider creds).
   - Usage examples (CLI/web, concurrency override, model override).
   - PoleCAT tool reference (read_file/write_file/list_directory/run_command).
   - Deployment summary pointing to Azure docs.
   - Testing summary (82 tests; categories).
   - Performance summary (throughput table from PERFORMANCE_REPORT).
   - Troubleshooting highlights.
   - Contributing, references, planned features/limits.

2) `docs/ARCHITECTURE.md`: deep dive
   - Agent roles and responsibilities.
   - State machines (bead lifecycle, run lifecycle).
   - Queues and communication.
   - Concurrency, locks, task cancellation.
   - Error handling/recovery.
   - Git worktree strategy.
   - Merge queue (bors-style bisect).
   - Data schema (SQLite tables).
   - Performance considerations.

3) `docs/API_REFERENCE.md`: FastAPI REST + WebSocket
   - Rigs: list/create/get.
   - Runs: start, status.
   - Beads: list/get.
   - Logs/events.
   - WebSocket stream format with example.
   - Error format/status codes.
   - cURL and JS examples.

4) `docs/AGENT_DEVELOPMENT.md`: how to build custom agents
   - BaseAgent interface, LLM call helpers.
   - Tool definition schema.
   - System prompt best practices.
   - Example custom agent.
   - Unit/integration testing patterns.
   - Integrating a custom agent into orchestrator.

5) `docs/CONFIG.md`: exhaustive config reference
   - Core env vars (host/port/db path/concurrency/stuck timeout/model).
   - Provider credentials (Anthropic/OpenAI/Azure).
   - Precedence (.env vs env vs defaults).
   - Example setups (budget, quality, production).
   - Troubleshooting config issues.

6) `docs/PERFORMANCE_TUNING.md`:
   - Benchmark pointers from PERFORMANCE_REPORT.
   - Throughput/latency/cost optimization.
   - Concurrency and model selection guidance.
   - Resource (CPU/RAM/disk) estimates.
   - Scaling strategies and monitoring snippets.

7) `docs/TROUBLESHOOTING.md`:
   - Install/runtime issues (API keys, rate limits, timeouts).
   - Agent stuck/nudges, tool timeouts.
   - DB locked/corrupt.
   - Git/worktree errors/conflicts.
   - Web dashboard/WebSocket issues.
   - Performance slow/memory growth.
   - Nuclear reset steps and what to collect for issues.

**Quality/format constraints**
- Use Markdown; concise, clear headings; tables where helpful.
- No boilerplate fluff; actionable steps and examples.
- Include code blocks with info strings.
- Keep performance tables aligned with existing PERFORMANCE_REPORT values:
  - Light: 1436 req/s, p95 0.7ms
  - Medium: 1943 req/s, p95 73.9ms
  - Heavy: 1202 req/s, p95 389.9ms
  - Very Heavy: 633 req/s, p95 134.0ms
- Testing summary: 82 tests total; categories unit/security/performance (all pass).
- Deployment: mention Azure Web App workflow uses OpenTofu; link to existing docs.
- Default model mention: `anthropic/claude-sonnet-4-6`.

**File paths to write**
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/API_REFERENCE.md`
- `docs/AGENT_DEVELOPMENT.md`
- `docs/CONFIG.md`
- `docs/PERFORMANCE_TUNING.md`
- `docs/TROUBLESHOOTING.md`

**Do NOT**
- Change code or workflows.
- Create extra summary markdown files beyond those listed.
- Remove existing docs.

When done, ensure files are saved; then run formatting if desired; provide a short summary of changes.
