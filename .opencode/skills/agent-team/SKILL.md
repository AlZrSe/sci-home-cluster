---
name: agent-team
description: Define a workflow with PM, SWE, QA, and On-Call Engineer roles to develop software using an orchestrator agent.
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: software development
---

## What I do
I orchestrate a team of specialized agents to develop software with clear role separation and verification steps:
1. The orchestrator (main session) creates a task from user needs or business requirements and adds it to the backlog.
2. The Product Manager (PM) agent grooms the task: writes a detailed spec with user stories, acceptance criteria, and test scenarios based on the backlog item.
3. The Software Engineer (SWE) agent implements the code and writes unit/integration tests based strictly on the PM's specifications.
4. The Tester (QA) agent runs the tests, verifies each acceptance criterion with evidence, and reports pass/fail status.
5. If QA rejects the implementation (fails tests or doesn't meet acceptance criteria), the task returns to the SWE with specific feedback for fixes.
6. If QA accepts the implementation, the PM performs a final acceptance review from the user's perspective, verifying it solves the original problem.
7. Only after PM acceptance does the orchestrator commit the code to the main branch and close the task in the tracker.
8. I track tasks using either GitHub Issues or a file-based tracker where filenames encode state (e.g., .todo.md, .groomed.md, .in-progress.md, .done.md).
9. The On-Call Engineer role monitors CI/CD pipelines after code is pushed and fixes any pipeline failures that occur.

## When to use me
Use this skill when you want to develop software with a structured, role-based workflow that ensures quality through verification steps and prevents agents from skipping steps or declaring work complete prematurely. This approach is particularly effective for:
- Projects requiring clear separation of concerns between planning, implementation, and verification
- Teams wanting to enforce definition of done through multiple verification steps
- Situations where user perspective validation is critical before considering work complete

## How to use me
Load this skill at the start of a software development session. The orchestrator should then:

### Backlog Management
- Create a backlog of tasks using either:
  * GitHub Issues (with labels like "ready-for-grooming", "in-progress", "needs-review")
  * File-based tracker in a `/tasks` directory with files named by state:
    - `001-todo.md` → `001-groomed.md` → `001-in-progress.md` → `001-done.md`

### Role Execution Loop
For each task in the backlog:
1. **Instruct the PM to groom**: Ask the PM agent to create a specification with user stories, acceptance criteria, and test scenarios for the next backlog item
2. **Wait for PM completion**: Confirm the PM has updated the task state and created spec documents
3. **Instruct the SWE to implement**: Ask the SWE agent to implement code and write tests based on the PM's specification
4. **Wait for SWE completion**: Confirm the SWE has updated the task state and pushed code
5. **Instruct the QA to verify**: Ask the Tester agent to run tests and verify acceptance criteria
6. **Handle QA results**: 
   * If QA passes → proceed to PM acceptance review
   * If QA fails → return task to SWE with specific feedback for fixes
7. **Instruct the PM to accept**: Ask the PM agent to perform final user perspective review
8. **Close the task**: Only after PM acceptance, update task to done state and commit code

### Process Adherence
- Enforce that code is only committed to main branch after PM acceptance
- Use the On-Call Engineer only when CI/CD pipeline failures occur after code push
- Refer to PROCESS.md in the repository for detailed workflow guidance
- Update CLAUDE.md with project-specific instructions as needed

## Notes
- Role definitions for PM, SWE, QA, and On-Call Engineer should be available as agent configurations (e.g., in .opencode/agents/ or via OpenCode agent configuration).
- The On-Call Engineer role monitors CI/CD after code is pushed and fixes pipeline failures.
- Keep the process documented in PROCESS.md in the repository for consistency.
- Consider creating template files in `.opencode/templates/` for:
  * User stories
  * Acceptance criteria
  * Test scenarios
  * Definition of Done checklists