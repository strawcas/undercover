# REVIEW OF THE AGENT CODE AFTER PLAN_HARNESS IMPLEMENTATION

The product is evolving from a pilot toward a production-ready version, and there are some coding issues and shortcuts that are unacceptable.

## The main concern

The path for handling a single claim is spread across many files, making it difficult to follow how the queue and claim agents work.

For example, the claim agent's business instructions live in the backend, its tools are nested functions in `ClaimTools`, its limits sit in middleware, and its deadline is carried implicitly through a context variable in `agent/execution.py`. The queue's check for unfinished work is in a middleware callback in `agent/queue.py`, rather than being apparent from the `run_queue` sequence.

A reader should be able to follow a queue run and a claim assessment without having to reconstruct unnecessary framework internals. Separate files and middleware are appropriate where they have a clear purpose; reducing the file count is not the goal.

## Findings to investigate

An earlier code review identified the following issues. Verify each one against the current code and tests before deciding how to fix it.

1. **Activity logging can cause an assessment to fail.** In `agent/tools.py`, activity events use a direct HTTP call without the retry handling used for many other backend operations. Each tool logs before and after its main operation. If saving an approval succeeds but the subsequent completion event fails, the helper can stop before making an eligible payment. The backend preserves the decision and flags the unpaid approval, but a transient logging failure can still require handler attention. Logging a model retry can also fail and interrupt the retry itself.

2. **Cleanup can hide the original error.** In `agent/queue_tools.py`, attempt finalization, a claim read and reporting happen in a `finally` block. If one of those operations fails while another exception is being handled, the cleanup exception can replace the original error. The run-level cleanup in `agent/queue.py` may have the same problem.

3. **Unexpected helper errors lose useful detail.** In `agent/queue_tools.py`, unexpected exceptions are reduced to a message containing only their type, such as `Agent run failed (TypeError)`. Handler-facing messages should not expose sensitive provider or customer information, but developers need enough safe diagnostic information to investigate a failure.

4. **The main workflow is difficult to discover.** The queue and claim workflows are split across tools, middleware, execution state, backend instructions and framework callbacks. Review whether their responsibilities and control flow can be made clearer without removing necessary safeguards.

5. **Important data has weak names and types.** In `agent/execution.py`, `Record` is defined as `dict[str, Any]` and is used for scopes, claims, attempts, completion checks and run results. This makes it hard to tell what fields are available and increases the chance of mixing up different kinds of data.

6. **The Ollama recovery advice is outdated.** In `agent/main.py`, some error messages tell the user to restart the backend to retry. The current product requires an explicit agent command; restarting the backend alone does not retry a claim. A claim with two failed assessments is left for handler review rather than automatically retried.

### Please follow these steps:

- Confirm that these issues exist and determine how they were allowed to happen.
- Fix the issues.
- Investigate whether similar issues exist in the backend and dashboard code. I want clean, idiomatic code for a PR. The agent implementation does not pass a simple code review.
- Retest after fixing.
- Update `PROCESS.md`, `CODE_STANDARDS.md`, and `STATUS.md` so that this never happens again. Let me know how you did that.
