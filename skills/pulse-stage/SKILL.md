---
name: pulse-stage
description: Record whether the current project's work stage needs the user's review before progress can continue, so the monsgeek-claude-pulse keyboard lighting shows it. Use when you conclude a stage whose output the user must read, comment on, approve or decide about (a drafted spec, a plan ready for sign-off, a task finished and awaiting acceptance), and again to clear it once the user has responded and work moves on.
---

# pulse-stage

The keyboard shows one of these after your turn ends:

- **review** (attention colour): a stage concluded and progress now needs the user. Nothing is
  blocking them, but nothing moves until they read, comment, approve or choose.
- **open** (subtle colour): the session is live and nothing is waiting on the user.

You decide which, by leaving a hint for the project. It persists across turns and sessions until
you change it, and a forgotten hint expires after a day.

## Commands

Run from anywhere inside the project. The hint is keyed by the project's git root.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pulsectl.py" stage review "spec drafted, needs your comments"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pulsectl.py" stage open "plan approved, implementing"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pulsectl.py" stage clear
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pulsectl.py" stage            # show the current hint
```

If `CLAUDE_PLUGIN_ROOT` is not set in your shell, the plugin directory is the one shown by
`claude plugin list` for monsgeek-claude-pulse.

## When to set `review`

Right before you end a turn in which you:

- delivered a draft, spec, plan, design, or proposal and are waiting for the user's verdict,
- finished a task and need acceptance before the next one starts,
- asked a question whose answer gates the next step, when the project's instructions ask you
  to track stages this way.

Keep the note short and concrete: what is waiting and what the user should do.

## When to set `open` or `clear`

At the start of the turn in which the user has responded and work continues, before you start
on the next stage. `open` with a note documents where things stand; `clear` just removes the
hint. Either way the attention colour goes away at the end of that turn.

## Do not

- Set `review` for routine replies, status updates, or rhetorical questions.
- Leave `review` set after the user has already acted on it.
- Edit `.pulse-stage` in the project unless the user keeps one there on purpose. That file is
  an override for people who want the hint inside the repository; `pulsectl.py stage` is the
  default and keeps the hint out of the project tree.
