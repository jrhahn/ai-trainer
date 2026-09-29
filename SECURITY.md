# Security

## Reporting a vulnerability

Please **do not open a public issue** for anything exploitable. Use GitHub's
[private vulnerability reporting](https://github.com/jrhahn/ai-trainer/security/advisories/new)
instead, or email the address on the maintainer's GitHub profile.

This is a single-maintainer hobby project, not a funded product: expect a reply
in days rather than hours, and no bounty. What you will get is a straight answer
about whether it is a real finding and what is being done.

## What this application holds

Worth stating plainly, because it shapes what counts as a serious bug here.
Every account contains an athlete's **health and training data** — heart rate,
power, weight, sleep and fatigue notes, injuries, and free-text conversation
with the coach. On top of that the database stores, encrypted at rest, each
user's Strava OAuth tokens and any AI provider API key they supplied.

So: anything that reads another user's data, that lets text reach a third-party
host, or that spends the operator's provider credit, is in scope and is taken
seriously.

## Where the details are

[`docs/security.md`](docs/security.md) describes the controls, why each one is
shaped the way it is, what an operator has to configure, and the limitations
that are known and accepted. It is written for whoever maintains this next —
including its unflattering parts.

## Scope notes

- **Self-hosted by design.** Anyone with root on the host has everything:
  the `.env`, the database, and the Fernet key that decrypts stored tokens.
  That is a property of the deployment model, not a bug to report.
- **The LLM has no tools.** It cannot call functions, read files or make
  requests. A prompt that makes the coach say something wrong is a quality
  problem; a prompt that makes it *do* something would be a security one.
- **Findings in dependencies** are welcome, but please check whether the path is
  actually reachable here before reporting.
