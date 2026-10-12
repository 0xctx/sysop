# 🔒 Security

## Reporting a problem
Please **do not open a public issue** for a security problem. Use GitHub's private vulnerability reporting on this repository ("Security" → "Report a vulnerability"). You'll get a reply within a week.

Especially welcome: a way around the guard hook (credential reads, destructive commands, forged sysop records, merges that skip the gates), a way to land a change without a sealed review, or a credential that reaches a worker or a log.

## What sysop does to keep you safe
- 🚧 **The guard** (`hooks/guard.py`) runs before every tool call in every Claude Code session where the plugin is installed. It blocks credential reads, destructive git commands, forged sysop records, direct merges of sysop branches and AI attribution.
- 🔏 **The land gate** refuses a merge without exactly one READY review, a script-written cross-vendor result, and a seal binding that review to the branch's current commit.
- 🧹 **Workers** get your environment minus Claude Code's own session identity.
- 🙈 **Secrets** are never printed by sysop's commands; the Kaggle tunnel token goes up once from your own terminal and is never shown.

## What you should know
- Free model providers may log prompts. Keep private code on providers you trust.
- The approve-in-place dialog runs one blocked git command when **you** press Run once; it is never offered for credentials, forged records or attribution, and never in a headless run.
