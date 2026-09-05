---name: software-engineer
description: Implements the code and writes tests based on the specifications provided by the Product Manager. Focuses on turning specifications into working software with appropriate test coverage.
model: nvidia/nemotron-3-super-120b-a12b:free
tools: [read, write, edit, glob, grep, question, todowrite, webfetch, bash]
permission:
  skill:
    "*": allow
  bash:
    "*": allow
  edit:
    "*.py": allow
    "*.js": allow
    "*.ts": allow
    "*.tsx": allow
    "*.jsx": allow
    "*.java": allow
    "*.cpp": allow
    "*.c": allow
    "*.h": allow
    "*.hpp": allow
    "*.cs": allow
    "*.go": allow
    "*.rs": allow
    "*.php": allow
    "*.rb": allow
    "*.swift": allow
    "*.kt": allow
    "*.scala": allow
    "*.sh": allow
    "*.yml": allow
    "*.yaml": allow
    "*.json": allow
    "*.xml": allow
    "*.html": allow
    "*.css": allow
    "*.scss": allow
    "*.md": allow  # Allow limited doc updates (README comments, etc.)
    "*.txt": allow
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
    "*.py": allow
    "*.js": allow
    "*.ts": allow
    "*.tsx": allow
    "*.jsx": allow
    "*.java": allow
    "*.cpp": allow
    "*.c": allow
    "*.h": allow
    "*.hpp": allow
    "*.cs": allow
    "*.go": allow
    "*.rs": allow
    "*.php": allow
    "*.rb": allow
    "*.swift": allow
    "*.kt": allow
    "*.scala": allow
    "*.sh": allow
    "*.yml": allow
    "*.yaml": allow
    "*.json": allow
    "*.xml": allow
    "*.html": allow
    "*.css": allow
    "*.scss": allow
    "*.md": allow  # Allow limited doc updates
    "*.txt": allow
---