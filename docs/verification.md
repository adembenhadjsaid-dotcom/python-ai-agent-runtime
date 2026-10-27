# M1 verification — 27 October 2026

The complete six-document Drive folder was read through the connected plugin
before implementing this milestone. Source snapshots and URLs are in sources/.
At M1 implementation completion, the originally empty workspace was a local Git
repository on m1-core with no commits or remote publication. Repository setup and
publication were requested separately afterward; the checks below record M1's
local verification.

## Final local checks

| Check | Actual result |
| --- | --- |
| Python 3.11.16 editable source install: .venv/bin/python -m pytest -q | 57 passed |
| Python 3.14.3 installed wheel: .superpowers/py314/bin/python -m pytest -q | 57 passed |
| .venv/bin/ruff check . | All checks passed |
| .venv/bin/ruff format --check . | All files formatted |
| uv build --quiet | Exit 0; wheel and source archive built |
| Installed-wheel example on Python 3.14 | created → running → succeeded; exit 0 |
| Installed-wheel import/metadata | Imported from site-packages; runtime dependencies [] |
| Archive inspection | Example/docs in sdist; py.typed and cleanup helper in wheel |

Development tools used: pytest 9.1.1, pytest-asyncio 1.4.1, Ruff 0.17.3.
The Python 3.14 environment installed the rebuilt wheel, without an editable
source install, before running the same suite. Tests/example invoke no services,
model APIs or agent CLIs. GitHub Actions configuration was authored but not run
remotely.

## Test coverage and review

18 model tests cover contract validation, immutable input copies, terminal-result
invariants, timezone-aware deadlines and independent cancellation tokens.
36 runtime tests cover success, typed failures, safe exception normalization,
invalid/mismatched output, relative timeout/deadline precedence, zero/expired
budgets, cancellation, correlation, timing, cleanup, runtime reuse and independent
execution state. Three cleanup-helper tests cover repeated cancellation,
teardown failure and teardown self-cancellation.

Tests were written before their corresponding implementation. Missing-contract
and missing-runtime failures were observed before implementation. A cleanup error
regression first reproduced a misleading timed_out result, then passed after the
drained exception was normalized.

An independent read-only reviewer read all six specs, code, tests, decisions,
README, packaging and CI. The reviewer reported two important findings:

1. A plain asynchronous finally can be interrupted if teardown begins before the
   runtime injects cancellation. Added and tested finish_cleanup, which shields
   and drains teardown before propagating cancellation; the adapter contract and
   README explicitly require it or an equivalent pattern for asynchronous cleanup.
   Integration tests cover token-aware teardown, natural completion interrupted
   by a deadline, and native cancellation during teardown.
2. A completed execution exception concurrent with token cancellation was
   misclassified as a cleanup failure. Its regression failed with FAILED, then
   passed with CANCELLED after tracking whether the worker was unfinished when
   Runtime began stopping it.

The final 57-test suites and Ruff checks above ran after those fixes.
No critical findings or deferred minor findings were reported.

## Remaining limits

M1 has no implementation blocker. It is an in-memory offline vertical slice.
Cleanup is cooperative: adapters must finish protected teardown; the runtime
cannot forcibly stop arbitrary in-process code. No OS sandbox or tool permission
engine is implemented or claimed.

Before real integrations: verify/pin ACP and Delegate Skills versions/commands,
test subprocess/session shutdown and enforce permissions at the real tool
boundary. Follow M2–M7 in order. License, final package naming, API stability and
support policy remain open; Python >=3.11 and agent_runtime are provisional local
implementation choices, not newly accepted project decisions.
