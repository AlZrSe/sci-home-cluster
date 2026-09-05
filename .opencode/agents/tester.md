---name: tester
description: Runs tests, checks each acceptance criterion, and reports pass or fail with evidence. Verifies that the implementation meets the specifications provided by the Product Manager.
model: nvidia/nemotron-3-super-120b-a12b:free
tools: [read, write, edit, glob, grep, question, todowrite, webfetch, bash]
permission:
  skill:
    "*": allow
  bash:
    "*": allow
  edit:
    "*test*.py": allow
    "*spec*.py": allow
    "*test*.js": allow
    "*spec*.js": allow
    "*test*.ts": allow
    "*spec*.ts": allow
    "*test*.java": allow
    "*spec*.java": allow
    "*test*.cpp": allow
    "*spec*.cpp": allow
    "test_*.py": allow
    "_test.py": allow
    "test_*.js": allow
    "_test.js": allow
    "test_*.ts": allow
    "_test.ts": allow
    "test_*.java": allow
    "_test.java": allow
    "*_test.py": allow
    "*_test.js": allow
    "*_test.ts": allow
    "*_test.java": allow
    "tests/**": allow
    "__tests__/**": allow
    "spec/**": allow
    "test-results/**": allow
    "coverage/**": allow
    "*.md": allow  # For test reports, README updates
    "*.txt": allow  # For test logs, reports
  glob:
    "*": allow
  grep:
    "*": allow
  read:
    "*": allow  # Need to read source to understand what to test
  question:
    "*": allow
  todowrite:
    "*": allow
  webfetch:
    "*": allow
  write:
    "*test*.py": allow
    "*spec*.py": allow
    "*test*.js": allow
    "*spec*.js": allow
    "*test*.ts": allow
    "*spec*.ts": allow
    "*test*.java": allow
    "*spec*.java": allow
    "*test*.cpp": allow
    "*spec*.cpp": allow
    "test_*.py": allow
    "_test.py": allow
    "test_*.js": allow
    "_test.js": allow
    "test_*.ts": allow
    "_test.ts": allow
    "test_*.java": allow
    "_test.java": allow
    "*_test.py": allow
    "*_test.js": allow
    "*_test.ts": allow
    "*_test.java": allow
    "tests/**": allow
    "__tests__/**": allow
    "spec/**": allow
    "test-results/**": allow
    "coverage/**": allow
    "*.md": allow
    "*.txt": allow
---