"""The tier contract: CI may get cheaper only when the change cannot need it.

Every case here is a way a change could be under-tested if the classifier were wrong.
Run: python3 -m unittest discover -s tier
"""
import json
import os
import tempfile
import unittest

from classify import CORE, NONE, STANDARD, ChangedFile, Config, classify, main

CFG = Config(
    docs=["docs/**/*.md", "*.md"],
    docs_exclude=["docs/schema-classification.md", "docs/playtest-reports/**"],
    full=["pnpm-lock.yaml", ".github/**", "convex/schema.ts"],
)


def f(path, lines=5, prev=None):
    return ChangedFile(path, lines, prev)


class Classify(unittest.TestCase):
    def test_table(self):
        cases = [
            ("docs only", [f("README.md"), f("docs/plans/a.md", 400)], NONE),
            ("nested md is not root *.md", [f("app/src/styles/README.md")], CORE),
            ("a docs file a test reads", [f("docs/schema-classification.md")], CORE),
            ("excluded subtree", [f("docs/playtest-reports/x/report.md")], CORE),
            ("code renamed into docs/", [f("docs/old.md", 0, prev="convex/turn.ts")], CORE),
            ("docs plus small code", [f("docs/a.md", 900), f("app/x.ts", 10)], CORE),
            ("full-path beats small", [f("pnpm-lock.yaml", 1)], STANDARD),
            ("full-path renamed away", [f("convex/old-schema.ts", 1, prev="convex/schema.ts")], STANDARD),
            ("too many lines", [f("app/x.ts", 51)], STANDARD),
            ("too many files", [f(f"app/{i}.ts", 1) for i in range(4)], STANDARD),
            ("nothing reported", [], STANDARD),
        ]
        for name, files, want in cases:
            with self.subTest(name):
                self.assertEqual(classify(files, CFG)[0], want)

    def test_no_docs_declared_means_nothing_is_docs(self):
        self.assertEqual(classify([f("README.md")], Config())[0], CORE)

    def test_full_label_overrides_docs(self):
        self.assertEqual(classify([f("README.md")], CFG, ["ci:full"])[0], STANDARD)


class FakeApi:
    """Stands in for GitHub at the HTTP seam; paths are the real REST paths."""

    def __init__(self, routes):
        self.routes = routes

    def get(self, path):
        for prefix, body in self.routes.items():
            if path.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body, {}
        raise KeyError(f"unrouted {path}")

    def paged(self, path, key=None, limit=3000):
        body, _ = self.get(path)
        return body[key] if key else body


MERGE, HEAD, BEFORE, AFTER = "m" * 40, "h" * 40, "b" * 40, "a" * 40
TESTED_TREE, OTHER_TREE = "1" * 40, "2" * 40


def run_main(event_name, event, routes):
    with tempfile.TemporaryDirectory() as tmp:
        ev, out = os.path.join(tmp, "event.json"), os.path.join(tmp, "out")
        with open(ev, "w") as fh:
            json.dump(event, fh)
        env = {
            "GITHUB_EVENT_PATH": ev, "GITHUB_EVENT_NAME": event_name, "GITHUB_OUTPUT": out,
            "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": MERGE, "INPUT_TOKEN": "t",
            "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/ci.yml@refs/heads/main",
            "INPUT_DOCS_PATHS": "*.md", "INPUT_JOB_NAME": "ci tier",
        }
        main([], env, make_api=lambda *_: FakeApi(routes))
        with open(out) as fh:
            return dict(line.split("=", 1) for line in fh.read().splitlines())["tier"]


PUSH = {"ref": "refs/heads/main", "before": BEFORE, "after": AFTER, "repository": {"default_branch": "main"}}


def push_routes(pushed_tree, compare_files):
    return {
        f"commits/{AFTER}/pulls": [{"number": 7, "merged_at": "x", "merge_commit_sha": AFTER, "head": {"sha": HEAD}}],
        "actions/workflows/ci.yml/runs": {"workflow_runs": [{"id": 99}]},
        "actions/runs/99/jobs": {"jobs": [{"name": "ci tier", "id": 5}]},
        "check-runs/5/annotations": [{"title": "ci-tier", "message": f"tier=standard tested-tree={TESTED_TREE}"}],
        f"git/commits/{AFTER}": {"tree": {"sha": pushed_tree}},
        f"compare/{BEFORE}...{AFTER}": {"files": compare_files},
    }


class PushToMain(unittest.TestCase):
    code = [{"filename": "app/a.ts", "additions": 100, "deletions": 0}]

    def test_skips_when_main_tree_is_exactly_what_the_pr_passed(self):
        self.assertEqual(run_main("push", PUSH, push_routes(TESTED_TREE, self.code)), NONE)

    def test_runs_the_change_tier_when_main_moved_since_the_pr_run(self):
        self.assertEqual(run_main("push", PUSH, push_routes(OTHER_TREE, self.code)), STANDARD)

    def test_docs_merge_after_main_moved_still_runs_nothing(self):
        docs = [{"filename": "README.md", "additions": 3, "deletions": 0}]
        self.assertEqual(run_main("push", PUSH, push_routes(OTHER_TREE, docs)), NONE)

    def test_api_failure_runs_the_full_suite(self):
        routes = push_routes(TESTED_TREE, self.code)
        routes[f"commits/{AFTER}/pulls"] = OSError("boom")
        self.assertEqual(run_main("push", PUSH, routes), STANDARD)

    def test_force_push_runs_the_full_suite(self):
        self.assertEqual(run_main("push", {**PUSH, "forced": True}, push_routes(TESTED_TREE, self.code)), STANDARD)


if __name__ == "__main__":
    unittest.main()
