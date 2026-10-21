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
            # t4647: KG #646 changed one DB test, ran on core with DB tests skipped, reddened master.
            ("python postgres test", [f("workers/test_document_coverage_postgres.py", 12)], STANDARD),
            ("postgres test renamed away", [f("workers/test_cov.py", 1, prev="workers/test_cov_postgres.py")], STANDARD),
            ("ts postgres test", [f("src/lib/finance/api/storage.postgres.test.ts", 3)], STANDARD),
            ("postgres test harness", [f("scripts/kg-ci-postgres-tests.sh", 2)], STANDARD),
            ("db test folder", [f("tests/db/test_orders.py", 2)], STANDARD),
            ("db-named test", [f("app/orders.db.test.ts", 2)], STANDARD),
            ("ordinary test stays core", [f("workers/test_document_coverage.py", 2)], CORE),
            ("postgres runbook is still docs", [f("docs/postgres-upgrade.md", 80)], NONE),
            ("word containing pg stays core", [f("app/upgrade.ts", 2)], CORE),
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

    def test_docs_include_names_a_non_runtime_directory_and_cannot_hide_product(self):
        """Dissidium t2887: verification-skill markdown is not product, but the
        default agent/skill excludes still force the full suite."""
        skill = [f(f".agents/skills/verify-dissidium/features/{i}.md", 20) for i in range(6)]
        self.assertEqual(classify(skill, DEFAULTS)[0], STANDARD)
        included = Config.from_inputs(docs_include=".agents/skills/verify-dissidium/**")
        self.assertEqual(classify(skill, included)[0], NONE)
        self.assertEqual(classify([f(".agents/skills/other/SKILL.md", 80)], included)[0], STANDARD)
        self.assertEqual(classify([f(".github/workflows/ci.yml", 1)], included)[0], STANDARD)
        renamed = f(".agents/skills/verify-dissidium/SKILL.md", 1, prev="src/README.md")
        self.assertEqual(classify([renamed], included)[0], CORE)
        broad = Config.from_inputs(docs_include="**")
        for path in ("src/README.md", "content/posts/a.md", "pages/home.md", "public/notes.md",
                     "tests/fixtures/a.md", "app/src/styles/README.md"):
            with self.subTest(path=path):
                self.assertNotEqual(classify([f(path, 80)], broad)[0], NONE)
        self.assertEqual(classify([f("src/app.ts", 80)], broad)[0], STANDARD)
        still_product = Config.from_inputs(docs="none", docs_include=".agents/skills/verify-dissidium/**")
        self.assertEqual(classify(skill, still_product)[0], STANDARD)

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

    def test_verification_skill_markdown_is_not_documentation_until_named(self):
        files = [changed(".agents/skills/verify-dissidium/SKILL.md", 40),
                 changed(".agents/skills/verify-dissidium/features/index.md", 20)]
        event, routes = pr(files)
        repo = {
            ".agents/skills/verify-dissidium/SKILL.md": "Verify\n",
            ".agents/skills/verify-dissidium/features/index.md": "Map\n",
            "AGENTS.md": "See .agents/skills/verify-dissidium/SKILL.md\n",
            "scripts/build.sh": "echo ok\n",
        }
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], STANDARD)
        included = run_main("pull_request", event, routes, repo,
                            extra_env={"INPUT_DOCS_INCLUDE": ".agents/skills/verify-dissidium/**"})
        self.assertEqual(included["tier"], NONE)

    def test_included_verification_doc_a_script_reads_is_code(self):
        event, routes = pr([changed(".agents/skills/verify-dissidium/SKILL.md", 40)])
        repo = {
            ".agents/skills/verify-dissidium/SKILL.md": "Verify\n",
            "scripts/read.sh": "cat .agents/skills/verify-dissidium/SKILL.md\n",
        }
        result = run_main("pull_request", event, routes, repo,
                          extra_env={"INPUT_DOCS_INCLUDE": ".agents/skills/verify-dissidium/**"})
        self.assertEqual(result["tier"], CORE)

    def test_docs_only_cited_in_comments_still_run_nothing(self):
        event, routes = pr([changed("docs/RUNBOOK.md", 4), changed("AGENTS.md", 2)])
        repo = {"docs/RUNBOOK.md": "", "AGENTS.md": "",
                "src/db.ts": "// See docs/RUNBOOK.md for the restore drill.\nexport const x = 1;\n",
                ".env.example": "# documented in AGENTS.md\nAPI_URL=\n",
                ".github/workflows/ci.yml": "    # AGENTS.md says why\n    run: pnpm test\n"}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], NONE)

    def test_doc_a_script_reads_on_a_code_line_is_code(self):
        event, routes = pr([changed("docs/RUNBOOK.md", 4)])
        repo = {"docs/RUNBOOK.md": "", "scripts/check.sh": "# check the runbook\ngrep -q drill docs/RUNBOOK.md\n"}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], CORE)

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

    def test_records_naming_a_doc_do_not_make_it_code(self):
        # t6067: logs and records under plans/ named new lane notes, so plans-only
        # pushes in automancer-auto ran the full suite (run 39616824682).
        event, routes = pr([changed("plans/lanes/wick.md", 2)])
        repo = {"plans/lanes/wick.md": "",
                "plans/queue/run.jsonl": '{"wrote": "plans/lanes/wick.md"}\n',
                "docs/recon/2026-10.json": '{"note": "see wick.md"}\n'}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], NONE)

    def test_a_manifest_listing_a_docs_full_path_is_code(self):
        event, routes = pr([changed("guides/STYLE.md", 2)])
        repo = {"guides/STYLE.md": "", "config/prompts.json": '["guides/STYLE.md"]\n'}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], CORE)

    def test_no_checkout_means_full_suite(self):
        event, routes = pr([changed("README.md")])
        self.assertEqual(run_main("pull_request", event, routes, repo_files=None)["tier"], STANDARD)

    def test_core_tier_hands_over_the_files_that_still_exist(self):
        event, routes = pr([changed("app/a.ts", 3), changed("app/gone.ts", 0, "removed"), changed("README.md")])
        result = run_main("pull_request", event, routes, {"app/a.ts": "", "README.md": ""})
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["changed-files"]), ["README.md", "app/a.ts"])

    def test_core_tier_names_the_tests_that_name_the_changed_code(self):
        event, routes = pr([changed("convex/lib/costEnvelope.ts", 2), changed("convex/engine/effects/index.ts", 1),
                            changed("tests/unit/own.test.ts", 1)])
        repo = {
            "convex/lib/costEnvelope.ts": "", "convex/engine/effects/index.ts": "", "tests/unit/own.test.ts": "",
            "tests/unit/cost.test.ts": 'import { price } from "../../convex/lib/costEnvelope";\n',
            "tests/unit/effects.test.ts": 'import { run } from "../../convex/engine/effects";\n',
            "tests/unit/py_test.py": "from convex.lib.costEnvelope import price\n",
            "tests/unit/other.test.ts": 'import { x } from "../../convex/lib/costEnvelopeHistory";\n',
            "tests/unit/unrelated.test.ts": 'import { y } from "../../convex/lib/audit";\n',
        }
        result = run_main("pull_request", event, routes, repo)
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["direct-tests"]),
                         ["tests/unit/cost.test.ts", "tests/unit/effects.test.ts", "tests/unit/own.test.ts",
                          "tests/unit/py_test.py"])

    def test_convex_generated_api_calls_select_the_access_control_tests(self):
        for module, status in (("feedback", "modified"), ("feedback", "removed"),
                               ("pipeline/data", "modified"), ("admin/reports/feedback", "modified"),
                               ("reports/index", "modified")):
            for namespace in ("api", "internal"):
                with self.subTest(module=module, status=status, namespace=namespace):
                    event, routes = pr([changed(f"convex/{module}.ts", 2, status)])
                    reference = f"{namespace}.{module.replace('/', '.')}"
                    repo = {
                        f"convex/{module}.ts": "",
                        "tests/access.test.ts": f"t.mutation({reference}.submit, {{}});\n",
                        "tests/direct.test.ts": f'import "../convex/{module}";\n',
                        "tests/other.test.ts": f"t.query({reference}History.list, {{}});\n",
                    }
                    if status == "removed":
                        del repo[f"convex/{module}.ts"]
                    result = run_main("pull_request", event, routes, repo)
                    self.assertEqual(result["tier"], CORE)
                    self.assertEqual(json.loads(result["direct-tests"]),
                                     ["tests/access.test.ts", "tests/direct.test.ts"])

    def test_unmatched_convex_change_cannot_skip_the_access_control_suite(self):
        for status, companion in (("modified", None), ("removed", None),
                                  ("modified", "tests/other.test.ts"),
                                  ("modified", "convex/covered.ts")):
            with self.subTest(status=status, companion=companion):
                files = [changed("convex/auth.ts", 2, status)]
                if companion:
                    files.append(changed(companion))
                event, routes = pr(files)
                repo = {"tests/other.test.ts": "test('unrelated', () => {});\n",
                        "convex/covered.ts": "",
                        "tests/covered.test.ts": 'import "../convex/covered";\n'}
                if status != "removed":
                    repo["convex/auth.ts"] = ""
                result = run_main("pull_request", event, routes, repo)
                self.assertEqual(result["tier"], STANDARD)
                self.assertEqual(json.loads(result["direct-tests"]), [])

    def test_small_change_that_many_tests_name_runs_the_full_suite(self):
        event, routes = pr([changed("convex/engine/transition.ts", 1)])
        repo = {"convex/engine/transition.ts": "",
                **{f"tests/t{i}.test.ts": 'import "../convex/engine/transition";\n' for i in range(3)}}
        result = run_main("pull_request", event, routes, repo, {"INPUT_CORE_MAX_TESTS": "2"})
        self.assertEqual(result["tier"], STANDARD)
        self.assertEqual(json.loads(result["direct-tests"]), [])

    def test_core_tier_finds_tests_beside_or_relatively_importing_the_change(self):
        event, routes = pr([changed("tools/site/build.mjs", 3), changed("deploy.json", 2)])
        repo = {
            "tools/site/build.mjs": "", "deploy.json": "{}",
            "tools/site/build.test.js": "test('x', () => {});\n",
            "tools/site/__tests__/render.test.js": 'import { build } from "../build.mjs";\n',
            "tests/deploy-config.test.ts": 'const cfg = read("deploy.json");\n',
            "tests/deploy-script.test.ts": 'run("scripts/deploy.sh");\n',
        }
        result = run_main("pull_request", event, routes, repo)
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["direct-tests"]),
                         ["tests/deploy-config.test.ts", "tools/site/__tests__/render.test.js",
                          "tools/site/build.test.js"])

    def test_a_dotfile_names_only_the_tests_that_name_it(self):
        event, routes = pr([changed("config/.release-notes.json", 2), changed(".notes.json", 1)])
        repo = {
            "config/.release-notes.json": "{}", ".notes.json": "{}",
            "tests/notes.test.ts": 'read("config/.release-notes.json"); read(".notes.json");\n',
            "tests/other.test.ts": 'readdirSync("config/");\n',
        }
        result = run_main("pull_request", event, routes, repo)
        self.assertEqual(json.loads(result["direct-tests"]), ["tests/notes.test.ts"])


PUSH = {"ref": "refs/heads/main", "before": BEFORE, "after": AFTER, "repository": {"default_branch": "main"}}


def push_routes(pushed_tree, compare_files, moved_files=None):
    """compare_files is the merged PR's diff; moved_files is what the pushed tree holds
    beyond the tree the PR run tested (and, for a merge commit, beyond its head)."""
    moved = compare_files if moved_files is None else moved_files
    tested = {"app/a.ts": "1", "CHANGELOG.md": "1", "README.md": "1"}
    pushed = {**tested, **{m["filename"]: "2" for m in moved}}

    def tree(files):
        return {"truncated": False, "tree": [{"path": "app", "type": "tree", "mode": "040000", "sha": "t"}] + [
            {"path": p, "type": "blob", "mode": "100644", "sha": sha} for p, sha in files.items()]}
    return {
        f"commits/{AFTER}/pulls": [{"number": 7, "merged_at": "x", "merge_commit_sha": AFTER, "head": {"sha": HEAD}}],
        "actions/workflows/ci.yml/runs": {"workflow_runs": [{"id": 99}]},
        "actions/runs/99/jobs": {"jobs": [{"name": "ci tier", "id": 5}]},
        "check-runs/5/annotations": [{"title": "ci-tier", "message": f"tier=standard tested-tree={TESTED_TREE}"}],
        f"git/commits/{AFTER}": {"tree": {"sha": pushed_tree}},
        f"git/trees/{TESTED_TREE}": tree(tested),
        f"git/trees/{pushed_tree}": tree(pushed),
        f"compare/{BEFORE}...{AFTER}": {"files": compare_files},
        f"compare/{HEAD}...{AFTER}": {"status": "ahead", "files": moved},
    }


class PushToMain(unittest.TestCase):
    code = [changed("app/a.ts", 100)]

    def test_skips_when_main_tree_is_exactly_what_the_pr_passed(self):
        self.assertEqual(run_main("push", PUSH, push_routes(TESTED_TREE, self.code))["tier"], NONE)

    def test_skips_after_a_partial_rerun_of_the_pr_run(self):
        # Re-running failed jobs gives the latest attempt a fresh copy of the tier
        # job with no annotation; the tested tree lives on the first attempt's job.
        routes = push_routes(TESTED_TREE, self.code)
        del routes["actions/runs/99/jobs"]
        routes = {"actions/runs/99/jobs?filter=latest": {"jobs": [{"name": "ci tier", "id": 6}]},
                  "actions/runs/99/jobs?filter=all": {"jobs": [{"name": "ci tier", "id": 6},
                                                               {"name": "ci tier", "id": 5}]},
                  "check-runs/6/annotations": [], **routes}
        self.assertEqual(run_main("push", PUSH, routes)["tier"], NONE)

    def test_skips_when_the_tier_job_has_another_name(self):
        # chief-of-staff's one job is "ci gate" and passes no job-name; its exactly
        # tested pushes ran the full suite (run 39654364400, 21 Oct 2026).
        routes = push_routes(TESTED_TREE, self.code)
        routes["actions/runs/99/jobs"] = {"jobs": [{"name": "ci gate", "id": 5}]}
        self.assertEqual(run_main("push", PUSH, routes)["tier"], NONE)

    def test_code_that_moved_main_since_the_pr_passed_runs_its_tier(self):
        routes = push_routes(OTHER_TREE, [changed("CHANGELOG.md")], moved_files=self.code)
        self.assertEqual(run_main("push", PUSH, routes, {"CHANGELOG.md": ""})["tier"], STANDARD)

    def test_only_docs_moved_main_since_the_pr_passed_runs_nothing(self):
        # ops, 21 Oct 2026: 21 of 44 push runs re-ran the full suite because a release
        # PR's changelog landed between the PR's passing run and its merge.
        routes = push_routes(OTHER_TREE, self.code, moved_files=[changed("CHANGELOG.md", 4)])
        self.assertEqual(run_main("push", PUSH, routes, {"CHANGELOG.md": ""})["tier"], NONE)

    def test_squash_merge_of_a_ci_change_after_docs_moved_main_runs_nothing(self):
        # t6084: automancer-auto PR #389 changed ci.yml and was squash-merged after
        # plans/ docs landed; the push re-ran the full suite (run 39658252274).
        routes = push_routes(OTHER_TREE, [changed(".github/workflows/ci.yml", 3), changed("plans/x.md", 9)],
                             moved_files=[changed("plans/x.md", 9)])
        routes[f"compare/{HEAD}...{AFTER}"] = {"status": "diverged", "files": []}
        result = run_main("push", PUSH, routes, {"plans/x.md": "", ".github/workflows/ci.yml": ""})
        self.assertEqual(result["tier"], NONE)

    def test_squash_merge_after_code_moved_main_runs_its_tier(self):
        routes = push_routes(OTHER_TREE, [changed(".github/workflows/ci.yml", 3)],
                             moved_files=[changed("plans/x.md", 9), changed("app/b.ts", 2)])
        routes[f"compare/{HEAD}...{AFTER}"] = {"status": "diverged", "files": []}
        self.assertEqual(run_main("push", PUSH, routes)["tier"], STANDARD)

    def test_without_a_passing_pr_run_the_whole_pr_diff_is_classified(self):
        routes = push_routes(OTHER_TREE, self.code, moved_files=[changed("CHANGELOG.md", 4)])
        routes["actions/workflows/ci.yml/runs"] = {"workflow_runs": []}
        self.assertEqual(run_main("push", PUSH, routes, {"CHANGELOG.md": ""})["tier"], STANDARD)


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
