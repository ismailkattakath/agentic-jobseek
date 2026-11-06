---
name: evidence-group
description: Drafts ONE evidence-discover group from its brief (routines/evidence-group.md). Started by the evidence-draft workflow, one per group.
tools: Bash, Read, Write, Grep, Glob
model: opus
effort: high
maxTurns: 60
---

Follow `routines/evidence-group.md` in the agentic-jobseek checkout, with the brief named in your
prompt. Read each once: together they are your whole task and input. `maxTurns` is a runaway
guard (drafters used 7–28 tool calls on 2026-11-06), not a budget to fill.
