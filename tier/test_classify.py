"""The tier contract: CI may get cheaper only when the change cannot need it.

Every case here is a way a change could be under-tested if the classifier were wrong.
Run: python3 -m unittest discover -s tier
"""
import json
import os
import subprocess
import tempfile
import unittest

from classify import CORE, NONE, STANDARD, ChangedFile, Config, classify, main

DEFAULTS = Config.from_inputs()


def f(path, lines=5, prev=None):
    return ChangedFile(path, lines, prev)


class Classify(unittest.TestCase):
    def test_built_in_defaults(self):
        cases = [
            ("root and docs/ markdown", [f("README.md"), f("docs/plans/a.md", 400)], NONE),
            ("markdown under src/ is product", [f("app/src/styles/README.md")], CORE),
            ("markdown under skills/ is product", [f("skills/x/SKILL.md")], CORE),
            ("markdown test fixtures", [f("tests/fixtures/a.md")], CORE),
            ("code renamed into docs/", [f("docs/old.md", 0, prev="convex/turn.ts")], CORE),
            ("docs plus small code", [f("docs/a.md", 900), f("app/x.ts", 10)], CORE),
            ("lockfile", [f("pnpm-lock.yaml", 1)], STANDARD),
            ("nested package.json", [f("apps/web/package.json", 1)], STANDARD),
            ("CI file", [f(".github/workflows/ci.yml", 1)], STANDARD),
            ("migration", [f("db/migrations/0042_add.sql", 3)], STANDARD),
            ("schema renamed away", [f("convex/old-schema.ts", 1, prev="convex/schema.ts")], STANDARD),
            ("too many lines", [f("app/x.ts", 51)], STANDARD),
            ("too many files", [f(f"app/{i}.ts", 1) for i in range(4)], STANDARD),
            ("nothing reported", [], STANDARD),
        ]
        for name, files, want in cases:
            with self.subTest(name):
                self.assertEqual(classify(files, DEFAULTS)[0], want)

    def test_repo_inputs_add_to_and_replace_defaults(self):
        self.assertEqual(classify([f("README.md")], Config.from_inputs(docs="none"))[0], CORE)
        self.assertEqual(classify([f("scripts/deploy.mjs", 1)], Config.from_inputs(full="scripts/deploy.mjs"))[0], STANDARD)
        self.assertEqual(classify([f("pnpm-lock.yaml", 1)], Config.from_inputs(full="scripts/deploy.mjs"))[0], STANDARD)
        self.assertEqual(classify([f("docs/x.md")], Config.from_inputs(docs_exclude="docs/x.md"))[0], CORE)

    def test_full_label_overrides_docs(self):
        self.assertEqual(classify([f("README.md")], DEFAULTS, ["ci:full"])[0], STANDARD)


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


def run_main(event_name, event, routes, repo_files=None, extra_env=None):
    """Run plan, then scan in a real git checkout of repo_files when plan asks for it."""
    with tempfile.TemporaryDirectory() as tmp:
        ev, work, temp = os.path.join(tmp, "event.json"), os.path.join(tmp, "work"), os.path.join(tmp, "temp")
        os.makedirs(temp)
        os.makedirs(work)
        with open(ev, "w") as fh:
            json.dump(event, fh)
        if repo_files is not None:
            for rel, text in repo_files.items():
                os.makedirs(os.path.dirname(os.path.join(work, rel)) or work, exist_ok=True)
                with open(os.path.join(work, rel), "w") as fh:
                    fh.write(text)
            subprocess.run(["git", "init", "-q"], cwd=work, check=True, timeout=30)
            subprocess.run(["git", "add", "-A"], cwd=work, check=True, timeout=30)
        env = {
            "GITHUB_EVENT_PATH": ev, "GITHUB_EVENT_NAME": event_name, "RUNNER_TEMP": temp,
            "GITHUB_WORKSPACE": work, "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": MERGE, "INPUT_TOKEN": "t",
            "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/ci.yml@refs/heads/main", "INPUT_JOB_NAME": "ci tier",
            **(extra_env or {}),
        }

        def step(phase):
            out = os.path.join(tmp, f"out-{phase}")
            main([], {**env, "INPUT_PHASE": phase, "GITHUB_OUTPUT": out}, make_api=lambda *_: FakeApi(routes))
            with open(out) as fh:
                return dict(line.split("=", 1) for line in fh.read().splitlines())

        result = step("plan")
        if result["needs-scan"] == "true":
            result = step("scan")
        return result


def pr(files):
    routes = {"pulls/3/files": files, f"git/commits/{MERGE}": {"tree": {"sha": TESTED_TREE}}}
    return {"pull_request": {"number": 3, "labels": []}}, routes


def changed(path, lines=1, status="modified"):
    return {"filename": path, "additions": lines, "deletions": 0, "status": status}


class PullRequest(unittest.TestCase):
    def test_docs_nobody_reads_run_nothing(self):
        event, routes = pr([changed("docs/plans/a.md", 40), changed("README.md")])
        repo = {"docs/plans/a.md": "", "README.md": "", "app/x.ts": "// see docs/plans/b.md\n"}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], NONE)

    def test_doc_a_test_reads_by_path_is_code(self):
        event, routes = pr([changed("docs/schema-classification.md", 2)])
        repo = {"docs/schema-classification.md": "",
                "tests/backup.test.ts": 'readFileSync(resolve(root, "docs/schema-classification.md"))\n'}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], CORE)

    def test_doc_in_a_folder_code_lists_is_code(self):
        event, routes = pr([changed("docs/playtest-reports/phase-9.md", 2)])
        repo = {"docs/playtest-reports/phase-9.md": "",
                "scripts/collect.mjs": 'readdirSync(join(root, "docs/playtest-reports"))\n'}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], CORE)

    def test_doc_found_by_distinctive_name_is_code(self):
        event, routes = pr([changed("guides/ONBOARDING.md", 2)])
        repo = {"guides/ONBOARDING.md": "", "scripts/build.py": 'glob("**/ONBOARDING.md")\n'}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], CORE)

    def test_no_checkout_means_full_suite(self):
        event, routes = pr([changed("README.md")])
        self.assertEqual(run_main("pull_request", event, routes, repo_files=None)["tier"], STANDARD)

    def test_core_tier_hands_over_the_files_that_still_exist(self):
        event, routes = pr([changed("app/a.ts", 3), changed("app/gone.ts", 0, "removed"), changed("README.md")])
        result = run_main("pull_request", event, routes, {"app/a.ts": "", "README.md": ""})
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["changed-files"]), ["README.md", "app/a.ts"])


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
    code = [changed("app/a.ts", 100)]

    def test_skips_when_main_tree_is_exactly_what_the_pr_passed(self):
        self.assertEqual(run_main("push", PUSH, push_routes(TESTED_TREE, self.code))["tier"], NONE)

    def test_runs_the_change_tier_when_main_moved_since_the_pr_run(self):
        self.assertEqual(run_main("push", PUSH, push_routes(OTHER_TREE, self.code))["tier"], STANDARD)

    def test_docs_merge_after_main_moved_still_runs_nothing(self):
        routes = push_routes(OTHER_TREE, [changed("README.md", 3)])
        self.assertEqual(run_main("push", PUSH, routes, {"README.md": ""})["tier"], NONE)

    def test_api_failure_runs_the_full_suite(self):
        routes = push_routes(TESTED_TREE, self.code)
        routes[f"commits/{AFTER}/pulls"] = OSError("boom")
        self.assertEqual(run_main("push", PUSH, routes)["tier"], STANDARD)

    def test_force_push_runs_the_full_suite(self):
        self.assertEqual(run_main("push", {**PUSH, "forced": True}, push_routes(TESTED_TREE, self.code))["tier"], STANDARD)


if __name__ == "__main__":
    unittest.main()
