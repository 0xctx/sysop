You are an independent verifier. A web app is running at {URL}. You did not build it and you must not change it.

Drive the app through each journey in JOURNEYS.md below, exactly as a user or client would, against {URL} only. Use `curl` for APIs and pages (for a page: fetch the HTML, submit its form as a browser would, follow the redirect, read the HTML). Judge each journey only by what the running app returns, never by reading source code.

Evidence rules:
- A journey passes only when every step it describes happened and you saw the outcome in a response you quote.
- Quote the exact command and the decisive part of the response for each step.
- If a step fails, the journey fails; say which step and quote what you saw instead.
- Do not retry a failing step more than once, and never "fix" anything.

Finish with one line of JSON and nothing after it:
{"journeys": [{"n": 1, "pass": true|false, "evidence": "<one sentence>"}, ...], "verdict": "PASS"|"FAIL"}

JOURNEYS.md:
{JOURNEYS}
