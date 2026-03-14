# AGENTS.md

## Project overview
This repository contains a batch pipeline that:
1. Fetches subscribers from Firestore
2. Analyzes FPL data
3. Generates HTML reports
4. Sends emails
5. Updates subscriber state

## General rules
- Keep pull requests small and focused.
- Do not change Firestore schema unless explicitly requested.
- Do not modify email sending behavior without tests.
- Avoid introducing secrets into the repository.

## Roles

### Project Manager
Responsible for planning tasks and breaking work into small PRs.

### Developer
Implements code changes with minimal impact.

### QA
Adds tests and checks edge cases.

### Security
Reviews Firestore access and handling of personal data.

### DevOps
Maintains GitHub Actions and deployment logic.
