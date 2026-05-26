# CLAUDE.md


## Working with infrastructure

MUST: ensure that all infrastructure configurations are captured in code, config, or the definitive deploy.sh script in this repo.
MUST: prefer updating code in this repo and redeploying over modifying running instances when possible

## Verifying Your Work

### 1. Separate hypotheses from facts

Plans contain claims about how the system behaves ("the default is X", "this service runs before that one", "the cache key is sufficient").
These are hypotheses until probed.
Don't re-assert them in status docs as confirmed by execution.
Execution confirms code ran.
It doesn't confirm the premises the code was built on.

Tag any claim in a status doc as `observed` (saw it with my own eyes), `inferred` (derived from something I observed), or `assumed` (in the plan, never checked).

### 2. Report one of: passed, failed, unverified

Not "done," not "complete," not "code complete."
"Passed" requires a named condition and an observed result.
"Unverified" is honest.
If a checklist exists but wasn't run, the status is unverified, not done.

### 3. Before the work: enumerate what could break. After the work: show evidence for each.

For each meaningful change, ask: what kinds of bugs can this introduce?
Relevant classes for this codebase:

- **Design-level fit.** Does this solve the actual problem, or the nearest shape-matching problem? Surface the premise and ask whether it's still right.
- **Concurrency.** Two of these at once — what happens?
- **Failure and recovery.** Kill or break the process at each external call. What leaks?
- **Idempotency.** Run it twice. Does state diverge?
- **Persistence across restart.** What survives process death, service restart, host reboot? What should?
- **External integrations.** The external system's actual behavior (query it), not the SDK docs or the code's belief.
- **Defaults and empty config.** Behavior with nothing configured — safe, or footgun?
- **Logic under normal inputs.** The thing tests would catch. Write the test.

Not every class applies to every change.
For each one that does, producing no evidence means the status is unverified.

### 4. Query the external system, not the code's model of it

For infra: `iptables -S`, `ss -tlnp`, `findmnt`, `pgrep`, `ip link`, the service's admin API.
For external services: their actual state via their API — what's in Caddy's config, what Stack Auth says about this user, what GCP actually has provisioned.
For app code: the database, the HTTP response, the queue — not "my function returned successfully."

Your code's belief about external state is the thing being tested.
It can't also be the evidence.

### 6. Raise architectural premises, don't just optimize within them

If the plan assumes a particular design and the agent notices a premise worth questioning — a simpler path, a wrong abstraction, a security model that doesn't hold — the agent flags it rather than silently executing.
"I executed the plan" is not the goal.
"I built the right thing" is.
If there's a premise concern, name it in the status doc even if the decision is to defer.

# Status report template

```
### <change item>
Hypotheses from the plan:
  - <H1> → observed | inferred | assumed
  - <H2> → observed | inferred | assumed

Bug classes considered:
  - <class>: <probe> → passed / failed / output
  - <class>: deferred, reason

Premise concerns: <anything worth raising, or "none">

Status: passed | failed | unverified
```

Unverified is allowed.
It's not "done."

# The meta-rule

Before declaring status, the agent asks:
*What are the ways this could be wrong — in design, in concurrency, in failure, in integration, in logic — and what evidence would rule each one out?*
If the list is shorter than the real answer, the status is premature.

## Agent-friendly CLI design principles

- fstak CLI commands must be non-interactive by default and safe for agents, scripts, and CI.
- Commands must fail with clear errors instead of prompting for missing input.
- Secret and env commands must require either an inline value or an explicit input source such as `--from-stdin` or `--from-env`.
- Bare commands like `fstak env set KEY` are invalid because agents can hang or mis-handle interactive prompts.

## Deployment meaning by change type

When asked to "deploy" a change, treat that as shipping to the real target environment for that surface, not just merging code.

- **Backend change:** deploy to the cloud backend (control-plane/runtime host), then verify behavior from the external system (for example: endpoint response, running process, logs).
  - For control-plane HTTP verification, prefer the public API surface (for example `https://api.fstak.runspx.com/...`) over host-local checks like `127.0.0.1:9000`, unless the task explicitly asks for host-local validation.
- **Frontend change:** ship the frontend deployment, then verify the deployed URL reflects the new behavior/content.
- **CLI change (`fstak/`):** rebuild and release the CLI, then ship it through Homebrew (tap/formula updated to the new release) so users can install/upgrade it.
