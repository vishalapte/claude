# CLAUDE.md

## Commands that ASK

The rules live in `permissions` in `~/.claude/settings.json`, and only there. The harness
enforces them; do not restate them here. What settings.json cannot hold is the intent:

- **Mutating asks. Irreversible is blocked.** Irreversibility is judged by audience, not
  by the verb. Pushing a feature branch or opening a PR inside my orgs is mutating: it
  asks. Leaving that boundary (a public repo, a gist, email), rewriting or deleting shared
  history (force-push, deleting a remote branch, pushing to `main`/`master`), or
  destroying data with no copy (`manage.py flush`) is irreversible: it is blocked. A
  command no rule matches is judged by the same line.
- **Commits and pushes are mine.** Approving a prompt is a per-action act, not a standing grant.
- **Only what `permissions.deny` names is blocked.** Everything else prompts at most, so do
  not tell me something is blocked when it is not: an agent that believes `git commit`
  cannot run will report a runbook as un-runnable and route around a prompt I would have
  approved.
- **The `manage.py` exclusions are a denylist.** A custom management command that writes
  is allowed until it is added to `permissions.ask`, or to `deny` if it cannot be undone.

## Threads

I move between topics mid-session and react to what you show me. Keep one current thread.
Give every other thread its own file, `.rclocal/threads/<tag>.md`, at the repo root.

- At session start, list `.rclocal/threads/` if it exists.
- If a message has no tag, treat it as part of the current thread.
- If a message starts `[tag]` and `<tag>` is the current thread, treat it as part of the current thread.
- If a message starts `[new: tag]`, create `.rclocal/threads/<tag>.md` and file the text verbatim.
- If that new thread is a question or idea that needs no file changes, start a background agent for it.
- Name the agent after the tag.
- Tell the agent to read what it needs and to write only to `.rclocal/threads/<tag>.md`.
- Tell the agent to return one line: "Answer for [tag] is in .rclocal/threads/<tag>.md".
- If the new thread needs file changes, park it. Do not start it.
- If a message starts `[tag]` for a parked thread, file the text under that thread. Do not act on it.
- Treat "table", "later", "for now", "separate conversation", "when you are done" and "after X" as `[new: tag]`.
- After you file a message, reply in one line ("Filed under [tag]"). Then continue the current step.
- If a tagged message arrives mid-step, finish the step first. Then file it.
- If a message says `now [tag]`, write where the current thread stands into its file.
- Then read the named file and work from it.
- If a message says `done [tag]`, set that file's status line to `done`.
- If I ask about something you showed me, answer in one or two lines. Do not change the current step.
- If that answer implies work, file it as a note in the current thread's file.
- Change the current step only when I give an instruction: fix, do, go, change, remove.
- If I ask "Sitrep?" during a task, report where that task stands.
- If no task is in progress, list the tasks that remain in each thread.
- In either case, name the thread answers I have not read.
- If a background notification arrives while my question is open, answer my question first.
- If git does not ignore `.rclocal/`, add it to `.git/info/exclude`.

Each thread file starts with one line: `status: open | answered | parked | done`.

**Why:** across about 1,600 of my messages, about 140 reacted to a detail in your output.
Agents usually turned each one into the next task, which is what muddies the work. One file
per thread has one writer, so no two agents write the same file. A file outside the context
survives compaction and new sessions.

## Replies

- Put the answer or verdict in the first sentence.
- Show at most 5 items. If there are more, say how many and ask before listing them.
- Use my words for things I named. Do not rename them.
- If a term is not plain English, say what it is in six words or fewer.
- If I say "I don't follow", rewrite shorter and plainer. Do not add detail.

**Why:** about 50 of my messages over 9 of 10 days said "I don't follow", "gibberish" or "too
much to read". The triggers were long lists, buried verdicts, jargon, and your words replacing mine.

## Approval

- Read "roger", "copy" or "ok" as acknowledgement only. Do not act on it.
- Read "wilco", "go" or "engage" as an instruction to execute.
- After a wilco, execute the plan or instruction you last showed me.
- If you cannot tell which type a message is, read it as roger. Tell me so.
- If a message is a question, answer it. Do not change code or docs to answer it.
- If you only record a message, start your reply with "Roger".
- If you execute, start your reply with "Wilco". Then name the task.
- Do not merge, rebase or fast-forward into main without a wilco for it in that turn.
- If more than one message arrives at the same time, read all of them before you act.

**Why:** agents read "ok" as approval. They read "Shouldn't they be fixed?" as an instruction.
Then they put the fixes on main. More than one message at the same time is one thought.

## Named Repos
Refer to a repo by slug. A slug resolves to `~/dev/<org>/<slug>`, one directory per GitHub
owner; find it with `ls -d ~/dev/*/<slug>`.

## Installed by repos
A repo's `./install` writes its own file beside this one. The line below is a placeholder that
racecar's `./install` replaces with the import of its `RACECAR.md`; without racecar it stays an
inert comment.

<!-- RACECAR -->
