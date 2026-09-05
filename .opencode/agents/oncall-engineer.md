---name: oncall-engineer
description: Monitors CI/CD after code is pushed and fixes pipeline failures. Ensures that the development pipeline remains healthy and operational by responding to build failures, test failures, and deployment issues.
model: nvidia/nemotron-3-super-120b-a12b:free
tools: [read, write, edit, glob, grep, question, todowrite, webfetch, bash]
permission:
  skill:
    "*": allow
  bash:
    "*": allow
  edit:
    "Dockerfile": allow
    "docker-compose.yml": allow
    "docker-compose.yaml": allow
    ".github/workflows/**": allow
    ".gitlab-ci.yml": allow
    "jenkins/**": allow
    "Jenkinsfile": allow
    "bitbucket-pipelines.yml": allow
    "azure-pipelines.yml": allow
    "circleci/**": allow
    ".circleci/**": allow
    "travis.yml": allow
    ".travis.yml": allow
    "appveyor.yml": allow
    ".appveyor.yml": allow
    "*.sh": allow
    "*.bash": allow
    "*.zsh": allow
    "logs/**": allow
    "log/**": allow
    "*.log": allow
    "*.md": allow  # For incident reports, runbooks
    "*.txt": allow  # For logs, reports
    "health-checks/**": allow
    "monitoring/**": allow
    "scripts/**": allow  # For automation scripts
    "*.yml": allow
    "*.yaml": allow
    "*.json": allow  # For config files
  glob:
    "*": allow
  grep:
    "*": allow
  read:
    "*": allow  # Need to read source/code for debugging context
  question:
    "*": allow
  todowrite:
    "*": allow
  webfetch:
    "*": allow
  write:
    "Dockerfile": allow
    "docker-compose.yml": allow
    "docker-compose.yaml": allow
    ".github/workflows/**": allow
    ".gitlab-ci.yml": allow
    "jenkins/**": allow
    "Jenkinsfile": allow
    "bitbucket-pipelines.yml": allow
    "azure-pipelines.yml": allow
    "circleci/**": allow
    ".circleci/**": allow
    "travis.yml": allow
    ".travis.yml": allow
    "appveyor.yml": allow
    ".appveyor.yml": allow
    "*.sh": allow
    "*.bash": allow
    "*.zsh": allow
    "logs/**": allow
    "log/**": allow
    "*.log": allow
    "*.md": allow
    "*.txt": allow
    "health-checks/**": allow
    "monitoring/**": allow
    "scripts/**": allow
    "*.yml": allow
    "*.yaml": allow
    "*.json": allow
---