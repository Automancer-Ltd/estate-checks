#!/usr/bin/env python3
"""Self-tests for the install timeout check (tracker t4456).

Each case is a workflow file written to a temporary repository, checked with
the same entry points the composite action uses, and asserted on the findings
it raises. The cases pin every rule against a fixture that must be flagged and
one that must not, so the check is proven to discriminate rather than exist.
Run: python3 scripts/test-install-timeouts.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "install-timeouts.py")

_spec = importlib.util.spec_from_file_location("install_timeouts", SCRIPT)
it = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(it)

REPOSITORY = "Automancer-Ltd/fixture"
ALLOW = frozenset({("automancer-ltd/fixture", ".github/workflows/ci.yml", "build")})
ALLOW_ANY_JOB = frozenset({("automancer-ltd/fixture", ".github/workflows/ci.yml", "*")})
ALLOW_ANY_REPO = frozenset({("*", ".github/workflows/ci.yml", "build")})

UNBOUNDED_PIP = """\
name: CI
on: pull_request
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Install tools
        run: |
          set -euo pipefail
          pip install requests
      - run: echo ok
"""

CLEAN = """\
name: CI
on: pull_request
jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Install tools
        run: |
          pip install requests
      - run: echo ok
"""


def run(text, repository=REPOSITORY, allowlist=frozenset(), name="ci.yml"):
    with tempfile.TemporaryDirectory() as tmp:
        workflows = os.path.join(tmp, ".github", "workflows")
        os.makedirs(workflows)
        with open(os.path.join(workflows, name), "w", encoding="utf-8") as handle:
            handle.write(text)
        return it.scan(tmp, workflows, repository, allowlist)


def rules(findings):
    return sorted(f.rule for f in findings)


class BoundedWorkflow(unittest.TestCase):
    def test_job_timeout_bounds_every_step(self):
        self.assertEqual(run(CLEAN), [])

    def test_step_timeout_bounds_the_install(self):
        text = UNBOUNDED_PIP.replace(
            "      - name: Install tools\n", "      - name: Install tools\n        timeout-minutes: 3\n"
        )
        self.assertEqual(run(text), [])

    def test_expression_timeout_declares_a_limit(self):
        text = UNBOUNDED_PIP.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    timeout-minutes: ${{ matrix.minutes }}\n",
        )
        self.assertEqual(run(text), [])

    def test_coreutils_timeout_bounds_the_command(self):
        text = UNBOUNDED_PIP.replace("pip install requests", "timeout 300 pip install requests")
        self.assertEqual(run(text), [])
        text = UNBOUNDED_PIP.replace("pip install requests", "timeout 5m pip install requests")
        self.assertEqual(run(text), [])

    def test_comments_and_echo_are_not_commands(self):
        text = UNBOUNDED_PIP.replace(
            "          pip install requests\n",
            "          # pip install requests\n          echo \"npm install once said\"\n",
        )
        self.assertEqual(run(text), [])

    def test_reusable_workflow_caller_job_is_exempt(self):
        text = (
            UNBOUNDED_PIP.split("    steps:")[0]
            + "    uses: ./.github/workflows/reusable.yml\n    secrets: inherit\n"
        )
        self.assertEqual(run(text), [])

    def test_plain_run_on_the_key_line_is_reported_on_that_line(self):
        findings = run(UNBOUNDED_PIP.replace("      - run: echo ok\n", "      - run: npm ci\n"))
        npm = [f for f in findings if "npm" in f.message]
        self.assertEqual(len(npm), 1)
        self.assertEqual(npm[0].line, 11)  # the `- run: npm ci` line


class UnboundedWorkflow(unittest.TestCase):
    def test_unbounded_pip_is_reported_on_the_command_line(self):
        findings = run(UNBOUNDED_PIP)
        self.assertEqual(rules(findings), ["install-unbounded"])
        self.assertEqual(findings[0].line, 10)  # `pip install requests` inside the block
        self.assertIn("'build'", findings[0].message)
        self.assertIn("'Install tools'", findings[0].message)
        self.assertIn("pip install", findings[0].message)
        self.assertIn("timeout-minutes", findings[0].message)

    def test_every_command_on_the_card_is_caught(self):
        commands = {
            "pip install requests": "pip install",
            "pip3 install requests": "pip install",
            "python3 -m pip install requests": "pip install",
            "python3.12 -m pip install requests": "pip install",
            "uv pip install --system requests": "uv pip install",
            "npm ci": "npm ci / npm install",
            "npm install -g typescript": "npm ci / npm install",
            "sudo apt-get install -y shellcheck": "apt-get install",
            "sudo apt install -y shellcheck": "apt-get install",
            "go install golang.org/x/tools/cmd/goimports@latest": "go install",
        }
        for command, label in commands.items():
            with self.subTest(command=command):
                findings = run(UNBOUNDED_PIP.replace("pip install requests", command))
                self.assertEqual(rules(findings), ["install-unbounded"], command)
                self.assertIn(label, findings[0].message)

    def test_two_unbounded_lines_report_twice(self):
        text = UNBOUNDED_PIP.replace(
            "          pip install requests\n",
            "          pip install requests\n          npm ci\n",
        )
        self.assertEqual(rules(run(text)), ["install-unbounded", "install-unbounded"])

    def test_unparsable_workflow_is_not_a_silent_pass(self):
        findings = run("jobs: [unbalanced")
        self.assertEqual(rules(findings), ["workflow-unparsable"])


class Allowlist(unittest.TestCase):
    def test_exact_row_allows_the_job(self):
        self.assertEqual(run(UNBOUNDED_PIP, allowlist=ALLOW), [])

    def test_wildcard_job_row_allows_the_job(self):
        self.assertEqual(run(UNBOUNDED_PIP, allowlist=ALLOW_ANY_JOB), [])

    def test_wildcard_repo_row_allows_the_job(self):
        self.assertEqual(run(UNBOUNDED_PIP, allowlist=ALLOW_ANY_REPO), [])

    def test_row_for_another_repository_does_not(self):
        other = frozenset({("automancer-ltd/somewhere-else", ".github/workflows/ci.yml", "build")})
        self.assertEqual(rules(run(UNBOUNDED_PIP, allowlist=other)), ["install-unbounded"])

    def test_row_for_another_job_does_not(self):
        other = frozenset({("automancer-ltd/fixture", ".github/workflows/ci.yml", "other")})
        self.assertEqual(rules(run(UNBOUNDED_PIP, allowlist=other)), ["install-unbounded"])

    def test_unknown_repository_only_matches_wildcard_rows(self):
        other = frozenset({("automancer-ltd/fixture", ".github/workflows/ci.yml", "build")})
        self.assertEqual(rules(run(UNBOUNDED_PIP, repository="", allowlist=other)),
                         ["install-unbounded"])

    def test_malformed_row_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "allowlist.tsv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("repo\tworkflow\tjob\n")  # three columns, no reason
            with self.assertRaises(it.AllowlistError):
                it.load_allowlist(path)

    def test_row_without_a_reason_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "allowlist.tsv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("repo\tworkflow\tjob\t\n")  # empty reason
            with self.assertRaises(it.AllowlistError):
                it.load_allowlist(path)

    def test_missing_allowlist_raises(self):
        with self.assertRaises(it.AllowlistError):
            it.load_allowlist("/nonexistent/allowlist.tsv")

    def test_shipped_allowlist_loads_and_is_empty(self):
        entries = it.load_allowlist(it.DEFAULT_ALLOWLIST)
        self.assertEqual(entries, frozenset())


class ExitCode(unittest.TestCase):
    """The check is a gate: findings, and a check that cannot run, exit 1."""

    def _script(self, tmp, text):
        workflows = os.path.join(tmp, ".github", "workflows")
        os.makedirs(workflows)
        with open(os.path.join(workflows, "ci.yml"), "w", encoding="utf-8") as handle:
            handle.write(text)
        return subprocess.run(
            [sys.executable, SCRIPT, "--root", tmp, "--repository", REPOSITORY, "--json"],
            capture_output=True,
            text=True,
            cwd=tmp,
            check=False,
        )

    def test_findings_fail_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._script(tmp, UNBOUNDED_PIP)
            self.assertEqual(proc.returncode, 1)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload[0]["rule"], "install-unbounded")

    def test_bounded_workflow_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._script(tmp, CLEAN)
            self.assertEqual(proc.returncode, 0)

    def test_bad_allowlist_fails_even_without_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._script(tmp, CLEAN)
            allowlist = os.path.join(tmp, "allowlist.tsv")
            with open(allowlist, "w", encoding="utf-8") as handle:
                handle.write("no tabs here\n")
            proc = subprocess.run(
                [sys.executable, SCRIPT, "--root", tmp, "--repository", REPOSITORY,
                 "--allowlist", allowlist, "--json"],
                capture_output=True, text=True, cwd=tmp, check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("allowlist", proc.stderr)


if __name__ == "__main__":
    unittest.main()
