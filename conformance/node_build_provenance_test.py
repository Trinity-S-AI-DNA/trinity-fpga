#!/usr/bin/env python3
"""Check fpga/openxc7-synth/build_trinet_node.py against specs/trinet/node_build_provenance.t27 (#849).

Every name and rule comes from the spec's constants; nothing is typed here a second time. The
script is imported and run in throwaway git repositories, never against this checkout's state, and
the end-to-end case stops at the prjxray-db check, so no yosys, nextpnr or board is needed.

    python3 conformance/node_build_provenance_test.py
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SPEC = os.path.join(ROOT, "specs", "trinet", "node_build_provenance.t27")


def spec_constants(path):
    out = {}
    for m in re.finditer(r"^pub const (\w+) : ([^=]+?) = (.+);$", open(path).read(), re.M):
        name, ty, value = m.group(1), m.group(2).strip(), m.group(3).strip()
        out[name] = json.loads(value) if (ty == "str" or ty.endswith("]str")) else int(value)
    return out


C = spec_constants(SPEC)
SCRIPT = os.path.join(ROOT, *C["SCRIPT_REL"].split("/"))
spec = importlib.util.spec_from_file_location("build_trinet_node", SCRIPT)
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

failures = []


def check(cond, what):
    print("%s  %s" % ("ok  " if cond else "FAIL", what))
    if not cond:
        failures.append(what)


def git(repo, *cmd):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", repo] + list(cmd), check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def make_repo(path, branch="node-build"):
    os.makedirs(path)
    git(path, "init", "-q", "-b", branch)
    with open(os.path.join(path, "a.txt"), "w") as f:
        f.write("one\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "one")
    return path


SRC_REPO, SRC_ENV, SRC_DEFAULT = C["REPO_SOURCES"]

# 1. the names the script uses are the spec's
check(B.REPO_ENV == C["ENV_VAR"], "script reads $%s" % C["ENV_VAR"])
check(SRC_ENV == C["ENV_VAR"], "the environment case is named after the variable")
check(B.SCRIPT_REL.replace(os.sep, "/") == C["SCRIPT_REL"], "SCRIPT_REL matches the spec")

# 2. resolution order: --repo, else a non-empty $TRI_FPGA_REPO (~ expanded), else ~/FALLBACK_DIR
home_default = os.path.join(os.path.expanduser("~"), C["FALLBACK_DIR"])
check(B.default_repo({}) == (home_default, SRC_DEFAULT), "unset -> ~/%s, %s" % (C["FALLBACK_DIR"], SRC_DEFAULT))
check(B.default_repo({C["ENV_VAR"]: ""}) == (home_default, SRC_DEFAULT), "empty -> default")
check(B.default_repo({C["ENV_VAR"]: "/srv/x"}) == ("/srv/x", SRC_ENV), "set -> its value, %s" % SRC_ENV)
check(B.default_repo({C["ENV_VAR"]: "~/wt"}) == (os.path.expanduser("~/wt"), SRC_ENV), "leading ~ expanded")

# 3. manifest.json carries MANIFEST_KEYS (read from the dict literal, not from a run)
tree = ast.parse(open(SCRIPT).read())
manifest_keys = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "manifest" for t in node.targets) \
            and isinstance(node.value, ast.Dict):
        manifest_keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
check(set(C["MANIFEST_KEYS"]) <= manifest_keys, "manifest has keys %s" % C["MANIFEST_KEYS"])

with tempfile.TemporaryDirectory() as tmp:
    tmp = os.path.realpath(tmp)
    repo = make_repo(os.path.join(tmp, "repo"))

    # 4. provenance fields, clean / dirty / untracked / detached / not git
    p = B.checkout_provenance(repo)
    check(list(p) == C["PROVENANCE_FIELDS"], "fields are %s" % C["PROVENANCE_FIELDS"])
    check(p["path"] == repo, "path is the real path")
    check(re.fullmatch("[0-9a-f]{%d}" % C["COMMIT_HEX_LEN"], p["commit"] or "") is not None
          and p["commit"] == git(repo, "rev-parse", "HEAD"), "commit is HEAD, %d hex" % C["COMMIT_HEX_LEN"])
    check(p["branch"] == "node-build" and p["dirty"] is False, "branch named, clean")
    open(os.path.join(repo, "build.log"), "w").write("x\n")
    check(B.checkout_provenance(repo)["dirty"] is False, "an untracked file is not dirty")
    open(os.path.join(repo, "a.txt"), "a").write("two\n")
    check(B.checkout_provenance(repo)["dirty"] is True, "a modified tracked file is dirty")
    git(repo, "checkout", "-q", "--", "a.txt")
    git(repo, "checkout", "-q", "--detach")
    check(B.checkout_provenance(repo)["branch"] == C["DETACHED_BRANCH"], "detached -> %s" % C["DETACHED_BRANCH"])
    plain = os.path.join(tmp, "plain")
    os.makedirs(plain)
    q = B.checkout_provenance(plain)
    check(q == {"path": plain, "commit": None, "branch": None, "dirty": None}, "not git -> nulls")

    prov = B.build_provenance(plain, SRC_ENV, os.path.join(repo, *C["SCRIPT_REL"].split("/")))
    check(list(prov) == C["MANIFEST_KEYS"], "build_provenance returns %s" % C["MANIFEST_KEYS"])
    check(prov["script"]["path"] == repo and prov["script"]["commit"] == git(repo, "rev-parse", "HEAD"),
          "script object names the script's checkout and commit")
    check(list(prov["repo"]) == C["PROVENANCE_FIELDS"] + [C["REPO_SOURCE_FIELD"]]
          and prov["repo"][C["REPO_SOURCE_FIELD"]] == SRC_ENV, "repo object adds %r" % C["REPO_SOURCE_FIELD"])

    # 5. the script's checkout is its path minus SCRIPT_REL
    fake = os.path.join(tmp, "wt", *C["SCRIPT_REL"].split("/"))
    check(B.script_checkout(fake) == os.path.join(tmp, "wt"), "script checkout strips SCRIPT_REL")
    check(B.script_checkout() == ROOT, "this script's checkout is this repository")

    # 6. end to end: the two report lines print before any tool runs (stops at the db check)
    def run(env_value, *extra):
        env = {k: v for k, v in os.environ.items() if k != C["ENV_VAR"]}
        if env_value is not None:
            env[C["ENV_VAR"]] = env_value
        r = subprocess.run([sys.executable, SCRIPT, "--out", os.path.join(tmp, "out"),
                            "--db-root", os.path.join(tmp, "no-db")] + list(extra),
                           capture_output=True, text=True, env=env)
        return r.stdout.splitlines()

    git(repo, "checkout", "-q", "node-build")
    open(os.path.join(repo, "a.txt"), "a").write("dirty\n")
    lines = run(repo)
    head = git(repo, "rev-parse", "HEAD")[:12]
    check(len(lines) >= 2 and lines[0].startswith(C["PRINT_SCRIPT_PREFIX"] + ROOT),
          "first line: %r + script checkout" % C["PRINT_SCRIPT_PREFIX"])
    check(len(lines) >= 2 and lines[1].startswith(C["PRINT_REPO_PREFIX"] + repo + " @ " + head)
          and (", %s)" % C["DIRTY_WORD"]) in lines[1] and lines[1].endswith("[%s]" % SRC_ENV),
          "second line: %r + $%s repo, commit, dirty" % (C["PRINT_REPO_PREFIX"], C["ENV_VAR"]))
    lines = run(repo, "--repo", plain)
    check(len(lines) >= 2 and lines[1] == "%s%s (not a git checkout) [%s]" % (C["PRINT_REPO_PREFIX"], plain, SRC_REPO),
          "--repo wins over $%s" % C["ENV_VAR"])

print("\n%d failure(s)" % len(failures))
sys.exit(1 if failures else 0)
