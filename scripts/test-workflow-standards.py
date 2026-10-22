#!/usr/bin/env python3
"""Self-tests for the workflow standards check (ci-standards rules 1-4).

Each case is a workflow file written to a temporary repository, checked with the
same entry point the composite action uses, and asserted on the rules it raises.
Run: python3 scripts/test-workflow-standards.py
"""
import importlib.util
import os
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "workflow-standards.py")

_spec = importlib.util.spec_from_file_location("workflow_standards", SCRIPT)
ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ws)

ORG = "Automancer-Ltd"
PERSONAL = "Waseemilyas"

CLEAN = """\
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  build:
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 5
    steps:
      - run: echo ok
"""


def run(text, owner=ORG, default_branch="main", name="ci.yml"):
    with tempfile.TemporaryDirectory() as tmp:
        workflows = os.path.join(tmp, ".github", "workflows")
        os.makedirs(workflows)
        with open(os.path.join(workflows, name), "w", encoding="utf-8") as handle:
            handle.write(text)
        return ws.scan(tmp, workflows, owner, default_branch)


def rules(findings):
    return sorted(f.rule for f in findings)


class CleanWorkflow(unittest.TestCase):
    def test_clean_workflow_has_no_findings(self):
        self.assertEqual(run(CLEAN), [])

    def test_personal_repository_may_use_hosted_labels(self):
        text = CLEAN.replace(
            "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}", "ubuntu-latest"
        )
        self.assertNotIn("runner-label-literal", rules(run(text, owner=PERSONAL)))
        self.assertIn("runner-label-literal", rules(run(text, owner=ORG)))


class Timeouts(unittest.TestCase):
    def test_missing_timeout_is_reported_on_the_job_line(self):
        text = CLEAN.replace("    timeout-minutes: 5\n", "")
        findings = run(text)
        self.assertEqual(rules(findings), ["job-missing-timeout"])
        self.assertEqual(findings[0].line, 14)  # the `build:` key
        self.assertIn("'build'", findings[0].message)

    def test_five_minutes_passes_and_ten_passes(self):
        self.assertEqual(run(CLEAN.replace("timeout-minutes: 5", "timeout-minutes: 10")), [])

    def test_over_the_cap_is_reported(self):
        findings = run(CLEAN.replace("timeout-minutes: 5", "timeout-minutes: 15"))
        self.assertEqual(rules(findings), ["job-timeout-over-cap"])
        self.assertIn("10-minute", findings[0].message)

    def test_reusable_workflow_caller_jobs_are_exempt(self):
        # Workflow syntax bans timeout-minutes (and runs-on) on a job that
        # calls a reusable workflow; the callee bounds its own jobs, so the
        # check must not demand a key the caller cannot declare.
        text = CLEAN.replace(
            "    runs-on: ${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}\n"
            "    timeout-minutes: 5\n"
            "    steps:\n"
            "      - run: echo ok\n",
            "    uses: ./.github/workflows/reusable.yml\n    secrets: inherit\n",
        )
        self.assertEqual(run(text), [])

    def test_reusable_workflow_caller_with_a_timeout_is_still_fine(self):
        # If a caller job carries the banned key anyway, the schema error is
        # actionlint's to report; this check has nothing to add.
        text = CLEAN.replace(
            "    runs-on: ${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}\n"
            "    steps:\n"
            "      - run: echo ok\n",
            "    timeout-minutes: 5\n    uses: ./.github/workflows/reusable.yml\n",
        )
        self.assertEqual(run(text), [])


class Concurrency(unittest.TestCase):
    def test_missing_concurrency_is_reported(self):
        text = CLEAN.split("jobs:")[0].replace(
            "concurrency:\n  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}\n  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n\n", ""
        ) + "jobs:" + CLEAN.split("jobs:")[1]
        self.assertEqual(rules(run(text)), ["workflow-missing-concurrency"])

    def test_short_form_group_passes(self):
        text = CLEAN.replace(
            "concurrency:\n  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}\n  cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            "concurrency: deploy-${{ github.ref }}",
        )
        self.assertEqual(run(text), [])

    def test_empty_concurrency_block_is_reported(self):
        text = CLEAN.replace(
            "concurrency:\n  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}\n  cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            "concurrency:\n  cancel-in-progress: false",
        )
        self.assertEqual(rules(run(text)), ["workflow-concurrency-no-group"])


class PushFilter(unittest.TestCase):
    def test_unfiltered_push_is_reported(self):
        text = CLEAN.replace("  push:\n    branches: [main]\n", "  push:\n")
        self.assertEqual(rules(run(text)), ["push-unfiltered"])

    def test_tag_only_push_passes(self):
        text = CLEAN.replace("  push:\n    branches: [main]\n", "  push:\n    tags: ['v*']\n")
        self.assertEqual(run(text), [])

    def test_branch_filter_without_the_default_branch_is_reported(self):
        text = CLEAN.replace("branches: [main]", "branches: [develop]")
        findings = run(text)
        self.assertEqual(rules(findings), ["push-wrong-branch"])
        self.assertIn("main", findings[0].message)

    def test_wildcard_branch_filter_including_the_default_passes(self):
        self.assertEqual(run(CLEAN.replace("branches: [main]", 'branches: ["*"]')), [])

    def test_yaml_boolean_on_key_is_still_read_as_a_trigger(self):
        # PyYAML resolves a bare `on:` key to boolean true; the check reads source text.
        text = CLEAN.replace("on:\n", "on:\n", 1)
        self.assertEqual(run(text), [])


class Precision(unittest.TestCase):
    def test_matrix_expression_runs_on_is_not_judged(self):
        text = CLEAN.replace(
            "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}", "${{ matrix.os }}"
        )
        self.assertEqual(run(text), [])

    def test_self_hosted_labels_are_not_judged(self):
        text = CLEAN.replace(
            "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}", "[self-hosted, linux]"
        )
        self.assertEqual(run(text), [])

    def test_unparsable_workflow_warns_instead_of_passing_silently(self):
        findings = run("name: CI\non: [\n")
        self.assertEqual(rules(findings), ["workflow-unparsable"])

    def test_missing_workflows_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ws.scan(tmp, os.path.join(tmp, ".github", "workflows"), ORG, "main"), [])

    def test_annotation_carries_file_and_line(self):
        findings = run(CLEAN.replace("timeout-minutes: 5", "timeout-minutes: 15"))
        self.assertEqual(
            (findings[0].path, findings[0].line), (".github/workflows/ci.yml", 16)
        )


SCHEDULED = """\
name: Nightly

on:
  schedule:
    - cron: '17 2 * * *'
  workflow_dispatch:

concurrency:
  group: nightly-${{ github.ref }}
  cancel-in-progress: false

jobs:
  suite:
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 5
    steps:
      - run: echo ok
"""

class ScheduleTrigger(unittest.TestCase):
    def test_schedule_trigger_is_reported_on_the_schedule_line(self):
        findings = run(SCHEDULED)
        self.assertEqual(rules(findings), ["workflow-schedule-trigger"])
        self.assertEqual(findings[0].line, 4)

    def test_sequence_form_schedule_is_reported(self):
        text = SCHEDULED.replace(
            "on:\n  schedule:\n    - cron: '17 2 * * *'\n  workflow_dispatch:\n",
            "on: [workflow_dispatch, schedule]\n",
        )
        findings = run(text)
        self.assertEqual(rules(findings), ["workflow-schedule-trigger"])
        self.assertIn("schedule", text.splitlines()[findings[0].line - 1])

    def test_workflow_without_a_schedule_is_not_reported(self):
        self.assertNotIn("workflow-schedule-trigger", rules(run(CLEAN)))

    def test_schedule_in_a_comment_or_job_name_is_not_a_trigger(self):
        text = "# the old schedule: cron was removed\n" + CLEAN.replace("  build:\n", "  schedule:\n")
        self.assertNotIn("workflow-schedule-trigger", rules(run(text)))

    def test_a_comment_cannot_silence_the_warning(self):
        text = "# estate-checks-allow: hosted-monitor-no-local-deps\n" + SCHEDULED
        self.assertIn("workflow-schedule-trigger", rules(run(text)))


class Ancors(unittest.TestCase):
    def test_anchored_job_is_followed(self):
        text = """\
name: CI
on:
  push:
    branches: [main]
  pull_request:
concurrency:
  group: ci-${{ github.ref }}
jobs:
  a: &job
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 5
    steps:
      - run: echo ok
  b: *job
"""
        self.assertEqual(run(text), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
