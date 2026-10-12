# Contributing

Thank you for wanting to help. Two things to know before you open a pull request.

## The licence

This code is published under the [Functional Source License](LICENSE): source
available, not open source. Read the README's licence section first. Your
contribution is published under the same licence.

## Sign off every commit (DCO)

Every commit in a pull request needs a `Signed-off-by:` line with your name and
the email address the commit is authored with:

```
Signed-off-by: Your Name <you@example.com>
```

With it you certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/):
you wrote the change, or have the right to submit it, under this project's
licence. `git commit -s` adds the line for you. To sign off commits you already
made:

```sh
git rebase --signoff origin/develop
git push --force-with-lease
```

The **DCO** check on your pull request fails and names each commit that has no
sign-off for its own author. A merge commit counts too, so prefer rebasing onto
`develop` over merging it in. Bot commits are exempt, and so are the
maintainer's own pull requests.

A contributor licence agreement may be added later, once outside contributions
arrive.

## Everything else

`CLAUDE.md` and `AGENTS.md` describe the conventions: tests, versions and the
changelog. Pull requests go against `develop`.
