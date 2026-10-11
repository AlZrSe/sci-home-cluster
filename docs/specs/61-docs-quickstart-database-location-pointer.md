# Spec: Quick Start Documentation Pointer to Database Location

## Issue
#61: The Quick Start documentation doesn't point to the database-location change documentation that was added in #34 (which anchored the default database path to the package location, not CWD).

## Context
- **Quick Start section**: Lines 140-165 in README.md
- **Database location documentation**: Lines 242-293 in README.md ("Which database am I using?")
- The database location change (#34) is a significant behavioral change that users following the Quick Start should be aware of

## User Story
As a developer following the Quick Start guide, I want to be directed to the database location documentation so that I understand where the database file will be created and how to override it if needed.

## Acceptance Criteria
1. The Quick Start section in README.md contains a reference/link to the "Which database am I using?" section
2. The reference is placed prominently where users will see it during initial setup
3. The reference explains why the database location matters (CWD-independent, single canonical file)

## Changes Required

### 1. Add pointer in Quick Start section (after line 155, before "Single Command")
Add a callout box or note in the Quick Start section that references the database location documentation:

```markdown
> **Note**: The database file location is CWD-independent and anchored to the repository root.
> See [Which database am I using?](#which-database-am-i-using) for details on the canonical
> database path, how to override it, and cleaning up stale database files from before issue #34.
```

### 2. Add pointer in Backend Setup section (after line 100)
Add a reference after the `alembic upgrade head` command:

```markdown
> **Note**: Migrations use the same CWD-independent database resolver as the server.
> See [Which database am I using?](#which-database-am-i-using) for details.
```

## Testing
- Verify the links work correctly in rendered markdown (GitHub, local preview)
- Verify the Quick Start section now references the database location documentation
- No code changes required - documentation only

## Definition of Done
- [ ] Spec created and reviewed
- [ ] README.md updated with pointers
- [ ] Links verified working
- [ ] PM acceptance