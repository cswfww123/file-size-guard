# file-size-guard

A ZCode plugin that blocks oversized Java and MyBatis mapper XML before they are committed.

Turning the plugin off in **Settings → Plugins** is the off switch — no `git config`, no per-clone setup.

## What it enforces

| Situation | Limit |
| --- | --- |
| New file under `src/main/` | 800 lines |
| New file under `src/test/` | 1200 lines |
| File listed in the baseline | may shrink or stay, never grow |
| Merge commit | only the files it introduces |
| `.gitattributes` `linguist-generated` | exempt (committed attributes only) |

Lines, not bytes. A Chinese comment is three bytes per character but one line, so a byte limit would push people to delete comments.

## Why a plugin and not a git pre-commit hook

A git hook is per-clone: every fresh clone has to run `git config core.hooksPath ...`, and `git commit --no-verify` skips it. A PreToolUse hook decides before git runs, so:

- enabling the plugin is the only setup step
- `--no-verify` is refused rather than obeyed
- disabling the plugin is a visible, reversible switch instead of an invisible local config

What it still cannot catch: `git cherry-pick` and `git rebase` are not `git commit` invocations. Run the checker over a range for those:

```bash
python3 hooks/check-commit.py --range <base> HEAD
```

## Install

Add this directory as a plugin marketplace in ZCode, then install the plugin. See the marketplace catalog in the repo root.

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `limit_main` | `800` | Line cap for `src/main/` |
| `limit_test` | `1200` | Line cap for `src/test/` |
| `baseline_path` | *(auto)* | Baseline file; defaults to `.file-size-baseline`, then `scripts/hooks/file-size-baseline.txt` |

## Baseline

A tab-separated list of files that were already oversized when the gate was turned on:

```
waba-user/.../GiftServiceImpl.java	1330
```

It only shrinks. Splitting a file means deleting its line in the same refactor commit. Adding a line to silence a block converts a violation into permanent permission, so don't.

## Tests

```bash
bash tests/check-commit-test.sh
```

Each case builds a throwaway repo in `/tmp`. The suite covers the threshold boundaries, the baseline ratchet, `--no-verify`, both attribute bypasses, and the merge rule.
