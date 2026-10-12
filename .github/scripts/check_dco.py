"""Every commit in a pull request carries a Developer Certificate of Origin sign-off.

ai-trainer-ops#23, #39 Q15. Reads the PR's commits as the GitHub API returns
them, one JSON object per line, on stdin
(``gh api 'repos/OWNER/REPO/pulls/N/commits?per_page=100' --paginate --jq '.[]'``), and
exits 1 when a commit lacks a ``Signed-off-by:`` trailer for its own author.

Exempt: bots, and the repository owner, who holds the rights and so has
nothing to certify to themselves.

    python check_dco.py --exempt jrhahn < commits.jsonl
    python check_dco.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys

_SIGNED_OFF = re.compile(r"^Signed-off-by:\s*(.+?)\s*<([^>]+)>\s*$", re.MULTILINE)


def unsigned(commits: list[dict], exempt: set[str]) -> list[str]:
    """One line per commit that needs a sign-off and lacks a matching one."""
    missing = []
    for item in commits:
        login = ((item.get("author") or {}).get("login") or "").lower()
        if login.endswith("[bot]") or login in exempt:
            continue
        commit = item["commit"]
        email = commit["author"]["email"].lower()
        signed = {mail.lower() for _, mail in _SIGNED_OFF.findall(commit["message"])}
        if email not in signed:
            subject = (commit["message"].splitlines() or [""])[0]
            missing.append(f"{item['sha'][:7]} {subject} (author {email})")
    return missing


def _commit(sha, login, email, message):
    return {
        "sha": sha,
        "author": {"login": login} if login else None,
        "commit": {"author": {"email": email}, "message": message},
    }


def self_test() -> None:
    signed = _commit("a" * 40, "someone", "s@example.com", "Fix\n\nSigned-off-by: S <S@example.com>")
    other = _commit("b" * 40, "someone", "s@example.com", "Fix\n\nSigned-off-by: X <x@example.com>")
    bare = _commit("c" * 40, None, "n@example.com", "")
    bot = _commit("d" * 40, "dependabot[bot]", "bot@example.com", "Bump")
    owner = _commit("e" * 40, "JRHahn", "o@example.com", "Merge")
    assert unsigned([signed, bot, owner], {"jrhahn"}) == []
    assert [line[:7] for line in unsigned([other, bare], {"jrhahn"})] == ["bbbbbbb", "ccccccc"]
    print("self-test ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exempt", action="append", default=[])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    commits = [json.loads(line) for line in sys.stdin if line.strip()]
    missing = unsigned(commits, {login.lower() for login in args.exempt})
    for line in missing:
        print(f"::error::No DCO sign-off for {line}. See CONTRIBUTING.md.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
