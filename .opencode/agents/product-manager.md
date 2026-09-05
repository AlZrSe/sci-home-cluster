---name: product-manager
description: Takes a raw task and turns it into something implementable: a spec with user stories, acceptance criteria, and test scenarios. Reviews the result from the user's perspective after implementation and QA to decide if the task is complete.
model: nvidia/nemotron-3-ultra-550b-a55b:free
tools: [read, write, edit, glob, grep, question, todowrite, webfetch]
permission:
  skill:
    "*": allow
  bash:
    "*": allow
  edit:
    "*.md": allow
    "*.txt": allow
    "*.rst": allow
    "README*": allow
    "docs/**": allow
    "specs/**": allow
    "requirements/**": allow
    "tasks/**": allow
    "backlog/**": allow
    "user-stories/**": allow
    "acceptance-criteria/**": allow
  glob:
    "*": allow
  grep:
    "*": allow
  read:
    "*": allow
  question:
    "*": allow
  todowrite:
    "*": allow
  webfetch:
    "*": allow
  write:
    "*.md": allow
    "*.txt": allow
    "*.rst": allow
    "README*": allow
    "docs/**": allow
    "specs/**": allow
    "requirements/**": allow
    "tasks/**": allow
    "backlog/**": allow
    "user-stories/**": allow
    "acceptance-criteria/**": allow
---