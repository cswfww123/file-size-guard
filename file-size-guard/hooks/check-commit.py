#!/usr/bin/env python3
"""PreToolUse(Bash): block `git commit` when the staged change adds or grows an
oversized Java / MyBatis mapper XML file.

Counted in lines, not bytes: a Chinese comment is three bytes per character but
still one line, so a byte limit would punish Chinese-commented code.

  new file         -> src/main 800 lines, src/test 1200 lines
  baselined file   -> may shrink or stay, never grow past its recorded size

Unlike a git pre-commit hook this also catches `--no-verify`, because the
decision happens before git runs. Disabling the plugin is the off switch.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MAIN = 800
DEFAULT_TEST = 1200

# `git commit`, `git -C dir commit`, `git commit -a`, but not `git log --grep=commit`
COMMIT_RE = re.compile(r"(?:^|[;&|]\s*|\bsudo\s+)git(?:\s+-C\s+\S+)?(?:\s+-\S+)*\s+commit\b")
NO_VERIFY_RE = re.compile(r"(?:^|\s)(?:--no-verify|-n)(?:\s|$)")
# `cd somewhere && git commit` commits the repo after the cd, not the workspace.
# Miss this and an agent working in a worktree or a scratch clone goes unchecked.
CD_RE = re.compile(r"(?:^|[;&|]\s*)cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")
GIT_C_RE = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


def deny(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


def read_stdin() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def run(args: list[str], cwd: Path) -> str:
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=20)
    except Exception:
        return ""
    return p.stdout


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def target_dir(command: str, cwd: Path) -> Path:
    """Where the commit actually runs.

    `cd repo && git commit` and `git -C repo commit` both commit a repo that is
    not the workspace. The workspace is only the fallback.
    """
    for pattern in (GIT_C_RE, CD_RE):
        found = pattern.search(command)
        if found:
            candidate = Path(_unquote(found.group(1)))
            if not candidate.is_absolute():
                candidate = cwd / candidate
            if candidate.is_dir():
                return candidate
    return cwd


def project_root(cwd: Path) -> Path | None:
    for cand in (str(cwd), os.environ.get("CLAUDE_PROJECT_DIR")):
        if not cand:
            continue
        out = run(["git", "rev-parse", "--show-toplevel"], Path(cand)).strip()
        if out:
            return Path(out)
    return None


def line_count(root: Path, spec: str) -> int:
    out = run(["git", "cat-file", "-p", spec], root)
    if not out:
        return 0
    return out.count("\n") + (0 if out.endswith("\n") else 1)


def limit_for(path: str) -> int:
    if "/src/test/" in path or path.startswith("src/test/"):
        return int(os.environ.get("FILE_SIZE_GUARD_LIMIT_TEST") or DEFAULT_TEST)
    return int(os.environ.get("FILE_SIZE_GUARD_LIMIT_MAIN") or DEFAULT_MAIN)


def load_baseline(root: Path) -> dict[str, int]:
    configured = os.environ.get("FILE_SIZE_GUARD_BASELINE")
    candidates = [Path(configured)] if configured else []
    candidates += [root / ".file-size-baseline", root / "scripts/hooks/file-size-baseline.txt"]
    for f in candidates:
        if f.is_file():
            table: dict[str, int] = {}
            for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if not raw.strip() or raw.lstrip().startswith("#"):
                    continue
                parts = raw.split("\t")
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    table[parts[0].strip()] = int(parts[1])
                else:
                    table[parts[0].strip()] = 0
            return table
    return {}


def in_scope(path: str) -> bool:
    if not (path.endswith(".java") or path.endswith("Mapper.xml") or ("/mapper/" in path and path.endswith(".xml"))):
        return False
    return True


def staged_changes(root: Path, new_only: bool, base: str = "", head: str = "") -> list[tuple[str, str, str]]:
    """Returns [(status, old_path, new_path)] for adds/copies/modifies/renames.

    With base and head, reads a commit range instead of the index — that is how
    cherry-pick and rebase, which never invoke a commit hook, get reviewed.
    """
    filt = "A" if new_only else "ACMR"
    if base and head:
        args = ["git", "diff", "--name-status", "-M", f"--diff-filter={filt}", "-z", base, head]
    else:
        args = ["git", "diff", "--cached", "--name-status", "-M", f"--diff-filter={filt}", "-z"]
    out = run(args, root)
    if not out:
        return []
    tokens = out.split("\0")
    rows: list[tuple[str, str, str]] = []
    i = 0
    while i < len(tokens):
        status = tokens[i]
        if not status:
            i += 1
            continue
        code = status[0]
        if code in ("R", "C"):
            if i + 2 >= len(tokens):
                break
            old, new = tokens[i + 1], tokens[i + 2]
            rows.append((status, old, new))
            i += 3
        else:
            if i + 1 >= len(tokens):
                break
            rows.append((status, tokens[i + 1], tokens[i + 1]))
            i += 2
    return rows


def is_generated(root: Path, path: str) -> bool:
    """Read attributes from the index so an untracked worktree file cannot exempt."""
    info_attrs = root / ".git" / "info" / "attributes"
    try:
        common = run(["git", "rev-parse", "--git-path", "info/attributes"], root).strip()
        if common:
            info_attrs = (root / common) if not os.path.isabs(common) else Path(common)
    except Exception:
        pass
    if info_attrs.is_file():
        # Local and untracked: while it exists, no exemption can be trusted.
        return False
    out = run(["git", "check-attr", "--cached", "linguist-generated", "--", path], root)
    return bool(re.search(r": (set|true)\s*$", out))


def main() -> None:
    # CLI mode for ranges the hook never sees (cherry-pick, rebase, --no-verify
    # in a clone where the plugin was off).
    argv = sys.argv[1:]
    if argv and argv[0] == "--range":
        if len(argv) < 3:
            print("用法: check-commit.py --range BASE HEAD", file=sys.stderr)
            sys.exit(2)
        root = project_root(Path(os.getcwd()))
        if root is None:
            print("不在 git 仓库内", file=sys.stderr)
            sys.exit(2)
        report(root, base=argv[1], head=argv[2])
        return

    data = read_stdin()
    if (data.get("tool_name") or data.get("tool") or "") != "Bash":
        allow()
    tool_input = data.get("tool_input") or {}
    command = str(tool_input.get("command") or "")
    if not COMMIT_RE.search(command):
        allow()

    # Resolve the repo from the command first: `cd elsewhere && git commit`
    # and `git -C elsewhere commit` do not commit the workspace.
    base_cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    cwd = target_dir(command, base_cwd)
    root = project_root(cwd)
    if root is None:
        allow()

    if NO_VERIFY_RE.search(command):
        deny(
            "file-size-guard 拒绝 --no-verify 提交。\n"
            "跳过 hook 会让体积检查失效；请去掉 --no-verify / -n 后重试。\n"
            "如果这道闸门确实不该管这次提交，请在「设置 → 插件」里停用 file-size-guard。"
        )

    # A merge carries the other branch's sizes; judge only files it introduces.
    merge_new_only = (root / ".git" / "MERGE_HEAD").exists()
    report(root, merge_new_only=merge_new_only)


def report(root: Path, merge_new_only: bool = False, base: str = "", head: str = "") -> None:
    baseline = load_baseline(root)
    violations: list[str] = []
    for status, old_path, new_path in staged_changes(root, merge_new_only, base, head):
        if not in_scope(new_path):
            continue
        if is_generated(root, new_path):
            continue
        spec = f"{head}:{new_path}" if base and head else f":{new_path}"
        lines = line_count(root, spec)
        if lines == 0:
            continue

        lookup = old_path if status.startswith("R") else new_path
        recorded = baseline.get(lookup)
        if recorded is not None:
            if lines > recorded:
                violations.append(
                    f"  {new_path}  {recorded} → {lines} 行，+{lines - recorded}（基线文件只许变小或持平）"
                )
            continue

        limit = limit_for(new_path)
        if lines > limit:
            violations.append(f"  {new_path}  {lines} 行（新文件，上限 {limit} 行）")

    if not violations:
        allow()

    where = f"{base[:9]}..{head} 区间" if base and head else "这次提交"
    deny(
        f"文件体积闸门（file-size-guard 插件）拦下了{where}：\n"
        + "\n".join(violations)
        + "\n\n规则：新文件 src/main ≤ "
        + str(os.environ.get("FILE_SIZE_GUARD_LIMIT_MAIN") or DEFAULT_MAIN)
        + " 行、src/test ≤ "
        + str(os.environ.get("FILE_SIZE_GUARD_LIMIT_TEST") or DEFAULT_TEST)
        + " 行；基线内文件只许变小或持平。\n\n"
        "按职责拆，不按体积切：\n"
        "  - Service/Manager：把一组内聚的方法（一条业务腿、一个场景）抽成新的 @Service 或 *Support 类，\n"
        "    原类注入后委托；对外接口和 Bean 名不变。\n"
        "  - 测试类：按场景拆成 <被测类><场景>Test，共享夹具抽到同包的 *TestFixtures。\n"
        "  - Mapper XML：按查询域拆出新的 Mapper 接口和 XML。\n"
        "  - 拆分单独做一个 [模块名]重构 提交，模块测试全绿后，再提交功能改动。\n"
        "不允许：XxxPart1 / Xxx2 / Util2 这类按体积切片，删注释、压行、改格式来凑数。\n"
        "拆完验证：mvn -q -pl <模块> -am test-compile，再跑该模块测试。\n"
        "生成代码豁免：在 .gitattributes 给该路径标 linguist-generated。\n"
        "停用闸门：「设置 → 插件」停用 file-size-guard。"
    )


if __name__ == "__main__":
    main()
