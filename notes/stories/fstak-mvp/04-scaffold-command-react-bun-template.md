# Story 4: Scaffold command for fixed React + Bun template

**Status:** failed

**Goal:** one-command project bootstrap.

**Completion criteria:**
- `fstak new <name>` generates the fixed template files.
- Template builds with one documented static build command.
- Generated project includes `.fstak/` state and can be deployed with `fstak run`.
- Name validation rejects invalid names deterministically.
- Command remains non-interactive and prints next steps.

**Gap:** current implementation uses React + TypeScript + Bun, but the static output behavior still needs verification against the Bun build pipeline.

**Files:**
- `fstak/src/commands/new.rs`
- `fstak/src/templates/*`
- `fstak/src/validation.rs`
