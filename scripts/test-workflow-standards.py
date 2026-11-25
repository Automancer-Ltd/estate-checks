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
    timeout-minutes: 20
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
        text = CLEAN.replace("    timeout-minutes: 20\n", "")
        findings = run(text)
        self.assertEqual(rules(findings), ["job-missing-timeout"])
        self.assertEqual(findings[0].line, 14)  # the `build:` key
        self.assertIn("'build'", findings[0].message)

    def test_the_floor_and_above_pass(self):
        for minutes in (20, 30, 45, 90):
            self.assertEqual(run(CLEAN.replace("timeout-minutes: 20", f"timeout-minutes: {minutes}")), [])

    def test_below_the_floor_is_reported(self):
        for minutes in (5, 10, 15, 19):
            findings = run(CLEAN.replace("timeout-minutes: 20", f"timeout-minutes: {minutes}"))
            self.assertEqual(rules(findings), ["job-timeout-below-floor"])
            self.assertIn("20-minute floor", findings[0].message)

    def test_expression_values_are_read(self):
        low = "timeout-minutes: ${{ needs.helper.result == 'skipped' && 10 || 5 }}"
        findings = run(CLEAN.replace("timeout-minutes: 20", low))
        self.assertEqual(rules(findings), ["job-timeout-below-floor"])
        self.assertIn(" 5,", findings[0].message)
        ok = "timeout-minutes: ${{ github.event_name == 'workflow_dispatch' && 30 || 20 }}"
        self.assertEqual(run(CLEAN.replace("timeout-minutes: 20", ok)), [])

    def test_numbers_in_an_expression_condition_are_not_limits(self):
        text = "timeout-minutes: ${{ github.event.pull_request.changed_files > 3 && 30 || 20 }}"
        self.assertEqual(run(CLEAN.replace("timeout-minutes: 20", text)), [])

    def test_an_expression_with_no_literal_is_not_judged(self):
        text = "timeout-minutes: ${{ fromJSON(vars.CI_TIMEOUT) }}"
        self.assertEqual(run(CLEAN.replace("timeout-minutes: 20", text)), [])

    def test_reusable_workflow_caller_jobs_are_exempt(self):
        # Workflow syntax bans timeout-minutes (and runs-on) on a job that
        # calls a reusable workflow; the callee bounds its own jobs, so the
        # check must not demand a key the caller cannot declare.
        text = CLEAN.replace(
            "    runs-on: ${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}\n"
            "    timeout-minutes: 20\n"
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
            "    timeout-minutes: 20\n    uses: ./.github/workflows/reusable.yml\n",
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
        findings = run(CLEAN.replace("timeout-minutes: 20", "timeout-minutes: 5"))
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
    timeout-minutes: 20
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
    timeout-minutes: 20
    steps:
      - run: echo ok
  b: *job
"""
        self.assertEqual(run(text), [])


PR_BASE = """\
name: CI
on:
  push:
    branches: [main]
  pull_request:
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
"""

JOB = """  {key}:
    runs-on: ${{{{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}}}
    timeout-minutes: 20
    steps:
      - uses: Automancer-Ltd/estate-checks/disk-ready@v1
      - uses: actions/checkout@v7
      - run: echo ok
"""


def pr_workflow(*keys):
    return PR_BASE + "".join(JOB.format(key=k) for k in keys)


class PrJobFanout(unittest.TestCase):
    def test_three_or_fewer_pr_jobs_pass(self):
        for count in (1, 2, 3):
            keys = [f"job{i}" for i in range(count)]
            self.assertNotIn("pr-job-fanout", rules(run(pr_workflow(*keys))))

    def test_four_pr_jobs_are_reported(self):
        findings = run(pr_workflow("a", "b", "c", "d"))
        self.assertIn("pr-job-fanout", rules(findings))
        finding = next(f for f in findings if f.rule == "pr-job-fanout")
        self.assertIn("4 PR-eligible jobs", finding.message)

    def test_workflow_without_pull_request_trigger_is_not_judged(self):
        text = pr_workflow("a", "b", "c", "d").replace("  pull_request:\n", "")
        self.assertNotIn("pr-job-fanout", rules(run(text)))

    def test_reusable_workflow_callers_do_not_count(self):
        caller = (
            "  reuse1:\n    uses: ./.github/workflows/r.yml\n"
            "  reuse2:\n    uses: ./.github/workflows/r.yml\n"
        )
        text = pr_workflow("a", "b") + caller
        self.assertNotIn("pr-job-fanout", rules(run(text)))

    def test_push_only_jobs_do_not_count(self):
        push_only = (
            "  deploy:\n    if: github.event_name == 'push'\n"
            "    runs-on: ${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}\n"
            "    timeout-minutes: 20\n    steps:\n      - run: echo ok\n"
        )
        text = pr_workflow("a", "b", "c") + push_only
        self.assertNotIn("pr-job-fanout", rules(run(text)))

    def test_inline_justification_silences_the_warning(self):
        lines = pr_workflow("a", "b", "c", "d").splitlines(keepends=True)
        # justification comment sits on a line near the fourth job key
        for i, raw in enumerate(lines):
            if raw.startswith("  d:"):
                lines[i] = "  # estate-checks-allow: pr-job-fanout matrix split keeps cold runs under 5 min\n" + raw
        text = "".join(lines)
        self.assertNotIn("pr-job-fanout", rules(run(text)))


class OversizedPaidRunner(unittest.TestCase):
    def test_big_blacksmith_runner_on_pr_is_reported(self):
        for size in (8, 16, 32):
            text = pr_workflow("a").replace(
                "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}",
                f"blacksmith-{size}vcpu-ubuntu-2404",
            )
            findings = run(text)
            self.assertIn("oversized-paid-runner", rules(findings), size)

    def test_small_blacksmith_runner_passes(self):
        for size in (2, 4):
            text = pr_workflow("a").replace(
                "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}",
                f"blacksmith-{size}vcpu-ubuntu-2404",
            )
            self.assertNotIn("oversized-paid-runner", rules(run(text)), size)

    def test_push_only_workflow_is_not_judged(self):
        text = pr_workflow("a").replace("  pull_request:\n", "").replace(
            "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}",
            "blacksmith-16vcpu-ubuntu-2404",
        )
        self.assertNotIn("oversized-paid-runner", rules(run(text)))

    def test_push_gated_job_is_not_judged(self):
        text = pr_workflow("a").replace(
            "  a:\n",
            "  a:\n    if: github.event_name == 'push'\n",
        ).replace(
            "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}",
            "blacksmith-32vcpu-ubuntu-2404",
        )
        self.assertNotIn("oversized-paid-runner", rules(run(text)))

    def test_expression_scaling_to_large_vcpu_is_reported(self):
        text = pr_workflow("a").replace(
            "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}",
            "${{ matrix.big && 'blacksmith-8vcpu-ubuntu-2404' || 'blacksmith-2vcpu-ubuntu-2404' }}",
        )
        self.assertIn("oversized-paid-runner", rules(run(text)))

    def test_vars_ci_runner_expression_passes(self):
        self.assertNotIn("oversized-paid-runner", rules(run(pr_workflow("a"))))

    def test_inline_justification_silences_the_warning(self):
        text = pr_workflow("a").replace(
            "    runs-on: ${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}\n",
            "    # estate-checks-allow: oversized-paid-runner compile needs 16 cores\n"
            "    runs-on: blacksmith-16vcpu-ubuntu-2404\n",
        )
        self.assertNotIn("oversized-paid-runner", rules(run(text)))


class BoatDiskReady(unittest.TestCase):
    def test_ci_runner_job_without_disk_ready_is_reported(self):
        text = pr_workflow("a").replace(
            "      - uses: Automancer-Ltd/estate-checks/disk-ready@v1\n", ""
        )
        findings = run(text)
        self.assertIn("boat-missing-disk-ready", rules(findings))

    def test_ci_runner_job_with_disk_ready_passes(self):
        self.assertNotIn("boat-missing-disk-ready", rules(run(pr_workflow("a"))))

    def test_literal_runner_job_is_not_judged(self):
        text = pr_workflow("a").replace(
            "      - uses: Automancer-Ltd/estate-checks/disk-ready@v1\n", ""
        ).replace(
            "${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}", "ubuntu-latest"
        )
        findings = run(text, owner=PERSONAL)
        self.assertNotIn("boat-missing-disk-ready", rules(findings))

    def test_local_disk_ready_path_counts(self):
        text = pr_workflow("a").replace(
            "      - uses: Automancer-Ltd/estate-checks/disk-ready@v1\n",
            "      - uses: ./disk-ready\n",
        )
        self.assertNotIn("boat-missing-disk-ready", rules(run(text)))

    def test_tiny_gate_job_without_repo_commands_passes(self):
        text = PR_BASE + """  gate:
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 20
    steps:
      - run: echo "gate ok"
"""
        self.assertNotIn("boat-missing-disk-ready", rules(run(text)))

    def test_reusable_caller_is_not_judged(self):
        text = PR_BASE + "  a:\n    uses: ./.github/workflows/r.yml\n"
        self.assertNotIn("boat-missing-disk-ready", rules(run(text)))


class ReadyForReview(unittest.TestCase):
    def test_ready_for_review_type_is_reported(self):
        text = pr_workflow("a").replace(
            "  pull_request:\n",
            "  pull_request:\n    types: [opened, synchronize, ready_for_review]\n",
        )
        findings = run(text)
        self.assertIn("pr-ready-for-review-trigger", rules(findings))

    def test_default_types_pass(self):
        text = pr_workflow("a").replace(
            "  pull_request:\n",
            "  pull_request:\n    types: [opened, synchronize, reopened]\n",
        )
        self.assertNotIn("pr-ready-for-review-trigger", rules(run(text)))

    def test_bare_pull_request_trigger_passes(self):
        self.assertNotIn("pr-ready-for-review-trigger", rules(run(pr_workflow("a"))))

    def test_no_pull_request_trigger_is_not_judged(self):
        text = pr_workflow("a").replace("  pull_request:\n", "")
        self.assertNotIn("pr-ready-for-review-trigger", rules(run(text)))


class UnguardedDeployJob(unittest.TestCase):
    def _deploy_job(self, if_clause=""):
        if_line = f"    if: {if_clause}\n" if if_clause else ""
        return (
            "  deploy:\n"
            + if_line
            + "    runs-on: ${{ fromJSON(vars.CI_RUNNER || '\"ubuntu-latest\"') }}\n"
            "    timeout-minutes: 20\n    steps:\n"
            "      - uses: Automancer-Ltd/estate-checks/disk-ready@v1\n"
            "      - run: ./deploy.sh\n"
        )

    def test_unguarded_deploy_job_is_reported(self):
        findings = run(PR_BASE + self._deploy_job())
        self.assertIn("unguarded-deploy-job", rules(findings))

    def test_guarded_deploy_job_passes(self):
        text = PR_BASE + self._deploy_job(
            "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        )
        self.assertNotIn("unguarded-deploy-job", rules(run(text)))

    def test_release_and_publish_names_are_reported(self):
        for key in ("release", "publish-npm"):
            job = self._deploy_job().replace("  deploy:", f"  {key}:")
            self.assertIn("unguarded-deploy-job", rules(run(PR_BASE + job)), key)

    def test_job_name_field_counts(self):
        job = self._deploy_job().replace("  deploy:", "  ship:").replace(
            "    runs-on:", "    name: Publish release\n    runs-on:"
        )
        self.assertIn("unguarded-deploy-job", rules(run(PR_BASE + job)))

    def test_deploy_job_in_push_only_workflow_passes(self):
        text = (PR_BASE + self._deploy_job()).replace("  pull_request:\n", "")
        self.assertNotIn("unguarded-deploy-job", rules(run(text)))

    def test_non_deploy_job_passes(self):
        self.assertNotIn("unguarded-deploy-job", rules(run(pr_workflow("build", "test"))))


SHA = "93fc81caea466d4bd7eb5ecd830ef4f793c2372a"
SECRET_JOB = """  deploy:
    if: github.event_name == 'push'
    runs-on: ${{ fromJSON(vars.CI_RUNNER || '"ubuntu-latest"') }}
    timeout-minutes: 20
    steps:
      - uses: Automancer-Ltd/estate-checks/disk-ready@v1.9.3
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - run: ./deploy.sh
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
"""
PINNED = "Automancer-Ltd/estate-checks/disk-ready@" + SHA + " # v1.9.3"
RULE = "tag-pinned-action-in-secret-job"


class TagPinnedActionInSecretJob(unittest.TestCase):
    """t26558: a moved tag runs new code beside a deploy key on the next run."""

    def test_tag_pinned_action_in_secret_job_is_reported_on_its_uses_line(self):
        findings = [f for f in run(PR_BASE + SECRET_JOB) if f.rule == RULE]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, 15)  # the disk-ready step
        self.assertIn("disk-ready@<sha> # v1.9.3", findings[0].message)
        self.assertIn("repos/Automancer-Ltd/estate-checks/commits/v1.9.3", findings[0].message)

    def test_commit_pinned_secret_job_passes(self):
        text = PR_BASE + SECRET_JOB.replace("Automancer-Ltd/estate-checks/disk-ready@v1.9.3", PINNED)
        self.assertNotIn(RULE, rules(run(text)))

    def test_github_token_alone_is_not_a_deploy_secret(self):
        text = PR_BASE + SECRET_JOB.replace("secrets.CLOUDFLARE_API_TOKEN", "secrets.GITHUB_TOKEN")
        self.assertNotIn(RULE, rules(run(text)))
        text = PR_BASE + SECRET_JOB.replace("secrets.CLOUDFLARE_API_TOKEN", "github.token")
        self.assertNotIn(RULE, rules(run(text)))

    def test_every_other_secret_form_counts(self):
        forms = {
            "index": "${{ secrets['CLOUDFLARE_API_TOKEN'] }}",
            "all": "${{ toJSON(secrets) }}",
        }
        for label, expr in forms.items():
            text = PR_BASE + SECRET_JOB.replace("${{ secrets.CLOUDFLARE_API_TOKEN }}", expr)
            self.assertIn(RULE, rules(run(text)), label)

    def test_an_environment_makes_a_job_secret_holding(self):
        job = SECRET_JOB.replace(
            "        env:\n          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}\n", ""
        ).replace("    timeout-minutes: 20\n", "    timeout-minutes: 20\n    environment: production\n")
        self.assertIn(RULE, rules(run(PR_BASE + job)))

    def test_workflow_env_secret_reaches_every_job(self):
        text = PR_BASE.replace(
            "jobs:\n", "env:\n  TOKEN: ${{ secrets.DAGGER_CLOUD_TOKEN }}\njobs:\n"
        ) + JOB.format(key="build")
        self.assertIn(RULE, rules(run(text)))

    def test_job_without_a_secret_may_follow_a_tag(self):
        self.assertNotIn(RULE, rules(run(pr_workflow("a"))))

    def test_reusable_caller_passing_secrets_is_judged(self):
        text = PR_BASE + (
            "  a:\n    uses: Automancer-Ltd/shared/.github/workflows/deploy.yml@main\n"
            "    secrets: inherit\n"
            "  b:\n    uses: ./.github/workflows/local.yml\n    secrets: inherit\n"
        )
        findings = [f for f in run(text) if f.rule == RULE]
        self.assertEqual(len(findings), 1)
        self.assertIn("deploy.yml@main", findings[0].message)

    def test_docker_image_needs_a_digest(self):
        step = "      - uses: docker://alpine:3.20\n"
        text = PR_BASE + SECRET_JOB.replace("Automancer-Ltd/estate-checks/disk-ready@v1.9.3", PINNED) + step
        self.assertIn(RULE, rules(run(text)))
        digest = "      - uses: docker://alpine@sha256:" + "a" * 64 + "\n"
        text = PR_BASE + SECRET_JOB.replace("Automancer-Ltd/estate-checks/disk-ready@v1.9.3", PINNED) + digest
        self.assertNotIn(RULE, rules(run(text)))

    def test_missing_disk_ready_hint_suggests_the_commit_in_a_secret_job(self):
        bare = SECRET_JOB.replace("      - uses: Automancer-Ltd/estate-checks/disk-ready@v1.9.3\n", "")
        hint = [f for f in run(PR_BASE + bare) if f.rule == "boat-missing-disk-ready"]
        self.assertEqual(len(hint), 1)
        self.assertIn("disk-ready@<sha>", hint[0].message)
        self.assertNotIn("disk-ready@v1`", hint[0].message)
        plain = pr_workflow("a").replace("      - uses: Automancer-Ltd/estate-checks/disk-ready@v1\n", "")
        hint = [f for f in run(plain) if f.rule == "boat-missing-disk-ready"]
        self.assertIn("disk-ready@v1`", hint[0].message)


class OwnActionsPinTheirSteps(unittest.TestCase):
    """A consumer that pins this repository's actions by commit (t26558) still runs
    whatever a nested tag points at, so every composite here pins its own steps."""

    def test_every_nested_uses_is_a_commit_or_local(self):
        import yaml

        loose = []
        for manifest in ("action.yml", "tier/action.yml", "disk-ready/action.yml"):
            with open(os.path.join(REPO_ROOT, manifest), encoding="utf-8") as handle:
                steps = (yaml.safe_load(handle).get("runs") or {}).get("steps") or []
            for step in steps:
                uses = step.get("uses") if isinstance(step, dict) else None
                if uses and ws._movable_ref(uses):
                    loose.append(f"{manifest}: {uses}")
        self.assertEqual(loose, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
