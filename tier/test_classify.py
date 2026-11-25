"""The tier contract: CI may get cheaper only when the change cannot need it.

Every case here is a way a change could be under-tested if the classifier were wrong.
Run: python3 -m unittest discover -s tier
"""
import json
import os
import subprocess
import tempfile
import unittest

from classify import (
    CORE,
    NONE,
    STANDARD,
    ChangedFile,
    Config,
    classify,
    glob_to_regex,
    main,
    parse_list,
)

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
            ("code renamed into docs/", [f("docs/old.md", 0, prev="convex/turn.ts")], STANDARD),
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
        self.assertEqual(classify([renamed], included)[0], STANDARD)
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


class GlobBraces(unittest.TestCase):
    """t23443: declared `{a,b}` patterns must match, not silently match nothing."""

    def test_brace_alternation_matches_each_alternative(self):
        g = glob_to_regex("tests/**/*.test.{ts,tsx}")
        self.assertTrue(g.match("tests/a.test.ts"))
        self.assertTrue(g.match("tests/nested/b.test.tsx"))
        self.assertFalse(g.match("tests/a.test.js"))
        self.assertFalse(g.match("src/a.test.ts"))

    def test_braces_compose_with_other_globs(self):
        g = glob_to_regex("docs/{guides,plans}/*.md")
        self.assertTrue(g.match("docs/guides/a.md"))
        self.assertTrue(g.match("docs/plans/b.md"))
        self.assertFalse(g.match("docs/other/c.md"))
        self.assertTrue(glob_to_regex("**/{a,b}/**/*.ts").match("x/a/y/t.ts"))

    def test_unbalanced_braces_stay_literal(self):
        self.assertTrue(glob_to_regex("a{b").match("a{b"))
        self.assertFalse(glob_to_regex("a{b").match("ab"))
        self.assertTrue(glob_to_regex("a}b").match("a}b"))
        self.assertTrue(glob_to_regex("a,b").match("a,b"))

    def test_input_parsing_keeps_a_braced_pattern_whole(self):
        self.assertEqual(parse_list("tests/**/*.test.{ts,tsx}"), ["tests/**/*.test.{ts,tsx}"])
        self.assertEqual(parse_list("a, {b,c} , d"), ["a", "{b,c}", "d"])
        self.assertEqual(parse_list("# comment\n{a,b}"), ["{a,b}"])


class FakeApi:
    """Stands in for GitHub at the HTTP seam; paths are the real REST paths."""

    def __init__(self, routes):
        self.routes = routes
        self.repo = "o/r"

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

    def test_docs_named_in_prose_or_skipped_by_name_run_nothing(self):
        # automancer-ops PR #976 ran the full suite: drain.py's prompt names AGENTS.md.
        event, routes = pr([changed("AGENTS.md", 2), changed("docs/RUNBOOK.md", 2)])
        repo = {"AGENTS.md": "", "docs/RUNBOOK.md": "",
                "drain/drain.py": 'PROMPT = """\n1. Read the project\'s AGENTS.md and the skills.\n"""\n',
                "src/msg.ts": 'const m = "See docs/RUNBOOK.md for the drill";\nconst n = "(see docs/RUNBOOK.md)";\n',
                "scripts/clutter.py": 'HOME_ALLOW = {\n    "AGENTS.md",\n}\n',
                "scripts/purge.sh": "find . -not -name 'AGENTS.md' -print0\n",
                "scripts/msg.sh": 'echo "Read AGENTS.md first"\n',
                "tests/fixture.test.sh": 'touch "$FIXTURE/docs/AGENTS.md"\n',
                "systemd/x.service": "[Unit]\nDocumentation=file:/opt/ops/docs/RUNBOOK.md\n"}
        self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], NONE)

    def test_doc_code_uses_as_a_path_is_code(self):
        readers = {"scripts/load.sh": 'RUNBOOK="${RUNBOOK:-$OPS_DIR/docs/RUNBOOK.md}"\n',
                   "scripts/size.sh": "wc -c AGENTS.md docs/RUNBOOK.md\n",
                   "scripts/size.py": 'size = (ROOT / "AGENTS.md").stat().st_size\n',
                   "ci/check.yml": 'run: test -s "$GITHUB_WORKSPACE/AGENTS.md"\n'}
        for path, text in readers.items():
            with self.subTest(path):
                event, routes = pr([changed("AGENTS.md", 2), changed("docs/RUNBOOK.md", 2)])
                repo = {"AGENTS.md": "", "docs/RUNBOOK.md": "", path: text}
                self.assertEqual(run_main("pull_request", event, routes, repo)["tier"], CORE)

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
        event, routes = pr([changed("app/a.ts", 3), changed("README.md")])
        result = run_main("pull_request", event, routes, {"app/a.ts": "", "README.md": "", "app/a.test.ts": ""})
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["changed-files"]), ["README.md", "app/a.ts"])

    def test_touched_files_names_deleted_and_renamed_from_paths(self):
        # t12481: automancer-dashboard path-filters its suites; a PR deleting a file
        # under packages/ while editing apps/tracker must still run the autodash step.
        rename = {**changed("apps/portal/new.ts", 1, "renamed"), "previous_filename": "apps/portal/old.ts"}
        files = [changed("apps/tracker/src/a.ts", 3), changed("packages/lifecycles/src/gone.ts", 0, "removed"),
                 rename]
        want = ["apps/portal/new.ts", "apps/portal/old.ts", "apps/tracker/src/a.ts", "packages/lifecycles/src/gone.ts"]
        event, routes = pr(files)
        core = run_main("pull_request", event, routes, {"apps/tracker/src/a.ts": "", "apps/portal/new.ts": ""})
        self.assertEqual(core["tier"], STANDARD)
        self.assertEqual(json.loads(core["changed-files"]), ["apps/portal/new.ts", "apps/tracker/src/a.ts"])
        self.assertEqual(json.loads(core["touched-files"]), want)
        event, routes = pr(files + [changed("pnpm-lock.yaml", 400)])
        standard = run_main("pull_request", event, routes)
        self.assertEqual(standard["tier"], STANDARD)
        self.assertEqual(json.loads(standard["touched-files"]), sorted(want + ["pnpm-lock.yaml"]))

    def test_a_delete_only_change_still_names_what_it_touched(self):
        event, routes = pr([changed("packages/lifecycles/src/gone.ts", 0, "removed")])
        result = run_main("pull_request", event, routes, {"packages/lifecycles/src/x.ts": ""})
        self.assertEqual(json.loads(result["changed-files"]), [])
        self.assertEqual(json.loads(result["touched-files"]), ["packages/lifecycles/src/gone.ts"])

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
        for module, status in (("feedback", "modified"),
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

    def test_deleted_or_renamed_code_runs_the_full_suite(self):
        # A test left importing a deleted or renamed module fails on main, and no name search
        # can see what else used it (t20703): any deleted or renamed code file is standard.
        renamed = {"filename": "lib/new.py", "previous_filename": "lib/old.py", "additions": 1, "deletions": 1,
                   "status": "renamed"}
        repo = {"lib/new.py": "", "tests/test_foo.py": "from lib.foo import x\n", "lib/bar.py": ""}
        for files in ([changed("lib/foo.py", 40, "removed")], [renamed]):
            event, routes = pr(files)
            result = run_main("pull_request", event, routes, repo)
            self.assertEqual(result["tier"], STANDARD)
            self.assertIn("code file", result["reason"])

    def test_a_line_break_in_a_file_name_cannot_rewrite_the_tier(self):
        event, routes = pr([changed("postgres\ntier=none\nz=")])
        self.assertEqual(run_main("pull_request", event, routes)["tier"], STANDARD)

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

    def test_setup_every_test_runs_needs_the_full_suite(self):
        # gdp-chorus names src/test/setup.ts only in vite.config.ts; a one-line change ran
        # core with no tests. tallow and the ERP listed their setup in full-paths by hand.
        configs = {
            "vitest-string": ("vite.config.ts", 'test: { setupFiles: "./src/test/setup.ts" }\n',
                              "src/test/setup.ts"),
            "vitest-array": ("vitest.config.ts", 'setupFiles: ["tests/setup/spend-ledger.ts"],\n',
                             "tests/setup/spend-ledger.ts"),
            "resolved": ("vitest.clockshift.config.mts",
                         'setupFiles: [path.resolve(import.meta.dirname, "test/clock.setup.mjs")],\n',
                         "test/clock.setup.mjs"),
            "nested-config": ("web/vitest.config.ts", "setupFiles: ['./setup']\n", "web/setup.ts"),
            "jest-package": ("package.json", '{"jest": {"setupFilesAfterEnv": ["<rootDir>/jest.setup.js"]}}\n',
                             "jest.setup.js"),
            "playwright": ("e2e/playwright.config.ts", "globalSetup: './global-setup.ts',\n",
                           "e2e/global-setup.ts"),
        }
        for name, (config, text, setup) in configs.items():
            with self.subTest(name):
                event, routes = pr([changed(setup, 2)])
                repo = {config: text, setup: "", "src/a.test.ts": "test('a', () => {});\n"}
                result = run_main("pull_request", event, routes, repo)
                self.assertEqual(result["tier"], STANDARD)
                self.assertIn(f"{setup} is test setup that {config} runs before every test", result["reason"])
                self.assertEqual(json.loads(result["direct-tests"]), [])

    def test_pytest_conftest_needs_the_full_suite(self):
        for path in ("conftest.py", "services/api/tests/conftest.py"):
            with self.subTest(path):
                self.assertEqual(classify([f(path, 1)], DEFAULTS)[0], STANDARD)

    def test_code_a_config_names_outside_test_setup_stays_core(self):
        event, routes = pr([changed("src/main.ts", 2)])
        repo = {"vite.config.ts": 'build: { input: "src/main.ts" }, test: { setupFiles: ["src/setup.ts"] }\n',
                "src/main.ts": "", "src/setup.ts": "", "src/main.test.ts": "test('m', () => {});\n"}
        result = run_main("pull_request", event, routes, repo)
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["direct-tests"]), ["src/main.test.ts"])

    def test_a_dotfile_names_only_the_tests_that_name_it(self):
        event, routes = pr([changed("config/.release-notes.json", 2), changed(".notes.json", 1)])
        repo = {
            "config/.release-notes.json": "{}", ".notes.json": "{}",
            "tests/notes.test.ts": 'read("config/.release-notes.json"); read(".notes.json");\n',
            "tests/other.test.ts": 'readdirSync("config/");\n',
        }
        result = run_main("pull_request", event, routes, repo)
        self.assertEqual(json.loads(result["direct-tests"]), ["tests/notes.test.ts"])


PUSH = {"ref": "refs/heads/main", "before": "b" * 40, "after": "a" * 40, "repository": {"default_branch": "main"}}


class ReleaseEvents(unittest.TestCase):
    """t20703: a release proof rests on these events, so none of them may be tiered, whatever
    the diff or an earlier passing pull-request run says. No API route is given: none is read."""

    def test_dispatch_push_to_the_default_branch_and_a_tag_run_the_full_suite(self):
        cases = [
            ("workflow_dispatch", {"ref": "refs/heads/main", "repository": {"default_branch": "main"}}),
            ("workflow_dispatch", {"ref": "refs/heads/other", "repository": {"default_branch": "main"}}),
            ("push", PUSH),
            ("push", {**PUSH, "ref": "refs/tags/v1.2.3"}),
            ("schedule", {}),
        ]
        for name, event in cases:
            with self.subTest(name=name, ref=event.get("ref")):
                result = run_main(name, event, {}, {"README.md": ""})
                self.assertEqual(result["tier"], STANDARD)

    def test_push_reuses_only_a_same_tree_standard_successful_ci_gate(self):
        routes = {
            f"git/commits/{MERGE}": {"tree": {"sha": TESTED_TREE}},
            f"git/commits/{HEAD}": {"tree": {"sha": TESTED_TREE}},
            "actions/runs?": {"workflow_runs": [{"id": 42, "event": "pull_request", "conclusion": "success",
                                                    "head_sha": HEAD, "head_repository": {"full_name": "o/r"}}]},
            "actions/runs/42/jobs": {"jobs": [{"id": 7, "name": "ci", "conclusion": "success"}]},
            "check-runs/7/annotations": [{"title": "ci-tier", "message": f"tier=standard tested-tree={TESTED_TREE}"}],
        }
        result = run_main("push", PUSH, routes)
        self.assertEqual(result["tier"], NONE)
        self.assertIn("42", result["reason"])
        for key, value in [
            (f"git/commits/{HEAD}", {"tree": {"sha": OTHER_TREE}}),
            ("actions/runs?", {"workflow_runs": []}),
            ("actions/runs?", RuntimeError("API unavailable")),
            ("actions/runs/42/jobs", {"jobs": [{"id": 7, "name": "ci", "conclusion": "failure"}]}),
            ("check-runs/7/annotations", [{"title": "ci-tier", "message": f"tier=core tested-tree={TESTED_TREE}"}]),
            ("check-runs/7/annotations", []),
        ]:
            with self.subTest(key=key, value=value):
                self.assertEqual(run_main("push", PUSH, {**routes, key: value})["tier"], STANDARD)
        self.assertEqual(run_main("push", PUSH, routes, extra_env={"INPUT_CI_FULL": "1"})["tier"], STANDARD)

    def test_ci_full_variable_forces_the_full_suite_on_a_docs_only_pull_request(self):
        event, routes = pr([changed("README.md", 3)])
        self.assertEqual(run_main("pull_request", event, routes, {"README.md": ""})["tier"], NONE)
        result = run_main("pull_request", event, routes, {"README.md": ""}, {"INPUT_CI_FULL": "1"})
        self.assertEqual((result["tier"], result["reason"]), (STANDARD, "repository variable CI_FULL is set"))
        self.assertEqual(run_main("pull_request", event, routes, {"README.md": ""}, {"INPUT_CI_FULL": " "})["tier"], NONE)


class Unsure(unittest.TestCase):
    """The planner names tests by reference and neighbour; where that proves nothing, it says so."""

    def scan(self, files, repo, env=None):
        event, routes = pr(files)
        return run_main("pull_request", event, routes, repo, env)

    def test_a_fixture_used_by_two_tests_runs_the_full_suite(self):
        repo = {"tests/fixtures/users.ts": "", "tests/a.test.ts": 'import "./fixtures/users";\n',
                "tests/b.test.ts": 'import "./fixtures/users";\n'}
        result = self.scan([changed("tests/fixtures/users.ts", 2)], repo)
        self.assertEqual(result["tier"], STANDARD)
        self.assertIn("used by 2 test files", result["reason"])

    def test_a_helper_used_by_one_test_stays_with_that_test(self):
        repo = {"tests/helpers/db.ts": "", "tests/a.test.ts": 'import "./helpers/db";\n',
                "tests/b.test.ts": "test(1)\n"}
        result = self.scan([changed("tests/helpers/db.ts", 2)], repo)
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["direct-tests"]), ["tests/a.test.ts"])

    def test_a_helper_no_test_names_runs_the_full_suite(self):
        repo = {"src/testUtils.ts": "", "tests/a.test.ts": "test(1)\n"}
        self.assertEqual(self.scan([changed("src/testUtils.ts", 2)], repo)["tier"], STANDARD)

    def test_a_code_file_no_test_names_runs_the_full_suite(self):
        repo = {"src/lib/price.ts": "", "tests/a.test.ts": "test(1)\n"}
        result = self.scan([changed("src/lib/price.ts", 2)], repo)
        self.assertEqual(result["tier"], STANDARD)
        self.assertIn("no test names src/lib/price.ts", result["reason"])

    def test_a_code_file_a_test_names_stays_core(self):
        repo = {"src/lib/price.ts": "", "tests/a.test.ts": 'import "../src/lib/price";\n'}
        self.assertEqual(self.scan([changed("src/lib/price.ts", 2)], repo)["tier"], CORE)

    def test_a_declared_brace_test_glob_names_its_tests(self):
        # t23443: mileage-claims declares tests/**/*.test.{ts,tsx}; the classifier
        # used to match nothing with it, so every small change ran the full suite.
        repo = {"src/lib/price.ts": "", "tests/a.test.ts": 'import "../src/lib/price";\n'}
        result = self.scan([changed("src/lib/price.ts", 2)], repo,
                           {"INPUT_TEST_GLOBS": "tests/**/*.test.{ts,tsx}"})
        self.assertEqual(result["tier"], CORE)
        self.assertEqual(json.loads(result["direct-tests"]), ["tests/a.test.ts"])

    def test_a_test_file_that_cannot_be_read_runs_the_full_suite(self):
        import classify
        repo = {"src/lib/price.ts": "", "tests/a.test.ts": 'import "../src/lib/price";\n'}
        old = classify.MAX_SCAN_BYTES
        classify.MAX_SCAN_BYTES = 5
        try:
            result = self.scan([changed("src/lib/price.ts", 2)], repo)
        finally:
            classify.MAX_SCAN_BYTES = old
        self.assertEqual(result["tier"], STANDARD)


class Reason(unittest.TestCase):
    def test_text_from_the_pull_request_cannot_carry_shell(self):
        event, routes = pr([changed("a/$(curl evil)`id`;\"x\".postgres.test.ts", 1)])
        result = run_main("pull_request", event, routes)
        self.assertEqual(result["tier"], STANDARD)
        self.assertRegex(result["reason"], r"^[A-Za-z0-9 .,:/#+=_-]+$")
        self.assertNotIn("$", result["reason"])


if __name__ == "__main__":
    unittest.main()
