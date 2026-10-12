# Fleet narration

When Session says `"explain": true`, open each action with its line, as written, once per ticket (`<id>` is the ticket). Each line says what the step protects.

- step 2: "Here are the tickets; nothing starts until you say go."
- **launch**: "Starting <id> in an isolated copy of your repo, so your own files stay untouched."
- **wait**: "Workers are building; I'll pick up when one finishes."
- **check**: "Running your tests on <id>; nothing moves on while they fail."
- **correct**: "Sending <id> back to its builder with what went wrong."
- **review**: "Getting <id> reviewed, including a second opinion from another AI vendor's model."
- **verify**: "Starting your app and having a fresh agent use <id> the way a person would."
- **land**: "Merging <id> into <base>, because you said so."; without authority: "Stopping before the merge: merging needs your word."
