#!/usr/bin/env bash
# Regression test for hooks/check-commit.py.
# Run: bash tests/check-commit-test.sh
# Each case builds a throwaway repo in /tmp, so it never touches a real checkout.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$HERE/../hooks/check-commit.py"
LIMIT_MAIN=800
LIMIT_TEST=1200
pass=0; fail=0

fresh_repo() {
  repo="$(mktemp -d /tmp/fsg-plugin.XXXXXX)"
  cd "$repo" || exit 1
  git init -q && git config user.email t@t && git config user.name t
  mkdir -p src/main/java src/main/resources/mapper src/test/java docs
}
lines() { awk -v n="$2" 'BEGIN{for(i=0;i<n;i++) print "int a" i ";"}' > "$1"; }
commit_all() { git add -A && git commit -q -m seed --no-verify; }
# run_hook <command> : feed a PreToolUse payload, return the hook's exit code
run_hook() {
  local cmd="$1"
  python3 - "$cmd" "$repo" <<'PY'
import json, os, sys
print(json.dumps({
    "tool_name": "Bash",
    "tool_input": {"command": sys.argv[1]},
    "session_id": "test",
}), end="")
os.environ["CLAUDE_PROJECT_DIR"] = sys.argv[2]
PY
}
expect() {  # expect <case> <0|1> <command>
  local name="$1" want="$2" cmd="$3"
  local payload
  payload=$(python3 - "$cmd" <<'PY'
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}, "session_id": "t"}))
PY
)
  printf '%s' "$payload" | CLAUDE_PROJECT_DIR="$repo" python3 "$HOOK" >/tmp/fsg-plugin-out.txt 2>&1
  local got=$?
  [ "$got" -ne 0 ] && got=1
  if [ "$got" -eq "$want" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $name: want $want got $got"; sed 's/^/    /' /tmp/fsg-plugin-out.txt; fi
  cd /tmp && rm -rf "$repo"
}

# --- out of scope commands: never touch the repo state, always allow ---
fresh_repo; lines src/main/java/Big.java $((LIMIT_MAIN+1)); git add -A
expect "git status is not a commit" 0 "git status"

fresh_repo; lines src/main/java/Big.java $((LIMIT_MAIN+1)); git add -A
expect "git log is not a commit" 0 "git log --oneline -5"

fresh_repo; lines src/main/java/Big.java $((LIMIT_MAIN+1)); git add -A
expect "commit message mentioning commit" 0 "git log --grep=commit"

# --- threshold behaviour ---
fresh_repo; lines src/main/java/Small.java 100; git add -A
expect "new small java passes" 0 "git commit -m ok"

fresh_repo; lines src/main/java/Big.java $((LIMIT_MAIN+1)); git add -A
expect "new java over main limit blocks" 1 "git commit -m big"

fresh_repo; lines src/test/java/BigTest.java $((LIMIT_MAIN+1)); git add -A
expect "new test under test limit passes" 0 "git commit -m ok"

fresh_repo; lines src/test/java/HugeTest.java $((LIMIT_TEST+1)); git add -A
expect "new test over test limit blocks" 1 "git commit -m big"

fresh_repo; lines src/main/resources/mapper/BigMapper.xml $((LIMIT_MAIN+1)); git add -A
expect "new mapper xml over limit blocks" 1 "git commit -m big"

fresh_repo; lines docs/huge.sql $((LIMIT_MAIN*3)); lines README.md $((LIMIT_MAIN*3)); git add -A
expect "sql and md are out of scope" 0 "git commit -m ok"

# --- baseline ratchet ---
fresh_repo; lines src/main/java/Legacy.java 2000; commit_all
printf 'src/main/java/Legacy.java\t2000\n' > .file-size-baseline
lines src/main/java/Legacy.java 2100; git add -A
expect "baseline file that grows blocks" 1 "git commit -m grow"

fresh_repo; lines src/main/java/Legacy.java 2000; commit_all
printf 'src/main/java/Legacy.java\t2000\n' > .file-size-baseline
lines src/main/java/Legacy.java 1900; git add -A
expect "baseline file that shrinks passes" 0 "git commit -m shrink"

fresh_repo; lines src/main/java/Legacy.java 2000; commit_all
printf 'src/main/java/Legacy.java\t2000\n' > .file-size-baseline
git add -A
expect "baseline file at same size passes" 0 "git commit -m same"

# --- the plugin's advantage over pre-commit: --no-verify is caught too ---
fresh_repo; lines src/main/java/Big.java $((LIMIT_MAIN+1)); git add -A
expect "--no-verify is refused outright" 1 "git commit --no-verify -m big"

# A small file isolates the flag check from the size check: the size gate would
# pass, so only the --no-verify rule can produce a block here.
fresh_repo; lines src/main/java/Small.java 100; git add -A
expect "--no-verify on a small file is still refused" 1 "git commit -n -m ok"

fresh_repo; lines src/main/java/Small.java 100; git add -A
expect "a plain commit on a small file passes" 0 "git commit -m ok"

# --- attributes: committed exemption works, local ones do not ---
fresh_repo; printf 'src/main/java/Gen.java linguist-generated\n' > .gitattributes
lines src/main/java/Gen.java $((LIMIT_MAIN*3)); git add -A
expect "committed linguist-generated is exempt" 0 "git commit -m gen"

fresh_repo; mkdir -p .git/info; printf '* linguist-generated\n' > .git/info/attributes
lines src/main/java/Gen.java $((LIMIT_MAIN*3)); git add -A
expect ".git/info/attributes cannot exempt" 1 "git commit -m sneaky"

# --- merge judges only what it introduces ---
fresh_repo; lines src/main/java/Legacy.java 2000; commit_all
printf 'src/main/java/Legacy.java\t2000\n' > .file-size-baseline
lines src/main/java/Legacy.java 2100; git add -A; git rev-parse HEAD > .git/MERGE_HEAD
expect "merge lets a baselined file through" 0 "git commit -m merge"

fresh_repo; lines src/main/java/Legacy.java 2000; commit_all
printf 'src/main/java/Legacy.java\t2000\n' > .file-size-baseline
lines src/main/java/Big.java $((LIMIT_MAIN+1)); git add -A; git rev-parse HEAD > .git/MERGE_HEAD
expect "merge still blocks a new oversized file" 1 "git commit -m merge"

echo "check-commit: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
