---
name: file-size-guard
description: Explains the file-size gate, how to read its block message, how to split an oversized Java or mapper XML file by responsibility, and how to maintain the baseline. Use when a commit is blocked for file size, when a file must be split, or when the baseline needs updating.
---

# file-size-guard

A ratchet on `*.java` and MyBatis mapper XML. It runs at `git commit` time through a PreToolUse hook, so `--no-verify` cannot skip it. Turning the plugin off in **Settings → Plugins** is the off switch.

## The rules

| Situation | Limit |
| --- | --- |
| New file under `src/main/` | 800 lines |
| New file under `src/test/` | 1200 lines |
| File listed in the baseline | may shrink or stay, never grow past its recorded size |
| Merge commit | only the files the merge introduces |
| `.gitattributes` `linguist-generated` | exempt (committed attributes only) |

SQL, Markdown, lockfiles and any non-Java/Mapper file are out of scope.

Lines, not bytes. A Chinese comment is three bytes per character but still one line; a byte limit would push people to delete comments.

## When a commit is blocked

The message names the file, its current and recorded size, and why. Read it before touching anything: a baseline file that grew by 40 lines and a new 2000-line file need different responses.

Split by responsibility, never by size:

- **Service / Manager** — move one cohesive group of methods (one business leg, one scenario) into a new `@Service` or `*Support` class and delegate from the original. Public interfaces and bean names stay unchanged.
- **Test class** — split by scenario into `<Subject><Scenario>Test`; move shared fixtures into `*TestFixtures` in the same package.
- **Mapper XML** — split by query domain into a new Mapper interface and XML.

Forbidden: `XxxPart1` / `Xxx2` / `Util2` slicing, deleting comments, squeezing lines, reformatting to game the count.

Do the split in its own `[模块名]重构` commit with the module tests green, then commit the feature change on top. Verify with `mvn -q -pl <module> -am test-compile` and then that module's tests.

## The baseline

The baseline is a list of files that were already oversized when the gate was turned on. It only shrinks.

Format — one file per line, path relative to the repo root, tab, the line count it was allowed to keep:

```
waba-user/waba-user-module-billing/src/main/java/.../GiftServiceImpl.java	1330
```

Default location is `.file-size-baseline` at the repo root; `scripts/hooks/file-size-baseline.txt` is also read. Override with the plugin's `baseline_path` setting.

When you split a baselined file, delete its line in the same refactor commit. The baseline's length is the remaining debt: it should only ever go down.

Never add a line to silence a block. Adding a line converts a real violation into permanent permission for that file to stay oversized.

## Exempting generated code

Add the path to `.gitattributes` with `linguist-generated`:

```
waba-user/.../generated/ProtoMapper.java linguist-generated
```

Only committed attributes count. Two local files are deliberately ignored, because either one would silently disable every exemption for every worktree:

- `.git/info/attributes` — while this file exists, the gate trusts no exemption at all
- an untracked `.gitattributes` in the working tree

## When the gate itself is wrong

The gate measures lines in the staged blob. If it reports a size you believe is wrong:

1. Check the staged blob, not the working tree: `git cat-file -p :path/to/File.java | wc -l`.
2. If a partially staged file is involved, that is expected — only what is being committed counts.

If the rule itself needs to change, change the plugin's `limit_main` / `limit_test` settings, not the baseline.
