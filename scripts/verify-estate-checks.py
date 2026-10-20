#!/usr/bin/env python3
"""Drive the action manifests against owned synthetic consumers, retaining evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = "fixture/consumer"
TREE = "1" * 40


def command(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True,
                          check=True, timeout=120).stdout.strip()


class ApiServer(HTTPServer):
    routes: dict[str, Any] = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server: ApiServer = self.server  # type: ignore[assignment]
        path = self.path.split("?", 1)[0].removeprefix(f"/repos/{REPO}/")
        body = server.routes.get(path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format: str, *args: Any) -> None:
        pass


def action(manifest: Path, work: Path, evidence: Path, env: dict[str, str],
           inputs: dict[str, str] | None = None) -> tuple[int, dict[str, str]]:
    """Execute the published shell steps; local checkout substitutes only runner checkout."""
    spec = yaml.safe_load(manifest.read_text())
    values = {f"inputs.{key}": str(value.get("default", ""))
              for key, value in spec.get("inputs", {}).items()}
    values.update({f"inputs.{key}": value for key, value in (inputs or {}).items()})
    values.update({"github.action_path": str(manifest.parent), "github.token": "fixture",
                   "github.event_name": env["GITHUB_EVENT_NAME"],
                   "github.event_path": env["GITHUB_EVENT_PATH"],
                   "github.repository_owner": "Automancer-Ltd",
                   "github.repository": env.get("GITHUB_REPOSITORY", REPO),
                   "github.event.repository.default_branch": "main"})

    def resolve(expression: str) -> str:
        for part in expression.strip().split(" || "):
            part = part.strip()
            value = part[1:-1] if part.startswith("'") else values.get(part, "")
            if value:
                return value
        return ""

    def expand(text: str) -> str:
        return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: resolve(m[1]), text)

    for index, step in enumerate(spec["runs"]["steps"]):
        if "if" in step:
            left, right = step["if"].split(" == ")
            if resolve(left) != resolve(right):
                continue
        if "uses" in step:
            if not step["uses"].startswith("actions/checkout@"):
                raise ValueError(f"Unimplemented runner step: {step['uses']}")
            command(["git", "rev-parse", "--is-inside-work-tree"], work)
            (evidence / f"{index}-checkout.log").write_text(
                "Using owned fixture checkout; hosted checkout is proved in the consumer PR.\n")
            continue
        output = evidence / f"{index}-output.txt"
        output.touch()
        step_env = {**env, "GITHUB_OUTPUT": str(output), "GITHUB_ACTION_PATH": str(manifest.parent)}
        step_env.update({key: expand(str(value)) for key, value in step.get("env", {}).items()})
        script = expand(step["run"])
        (evidence / f"{index}-command.sh").write_text(script)
        proc = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                              cwd=work, env=step_env, capture_output=True, text=True, timeout=180)
        (evidence / f"{index}-log.txt").write_text(proc.stdout + proc.stderr)
        for line in output.read_text().splitlines():
            key, value = line.split("=", 1)
            values[f"steps.{step.get('id', index)}.outputs.{key}"] = value
        if proc.returncode:
            return proc.returncode, {}
    (evidence / "resolved.json").write_text(json.dumps(values, indent=2))
    return 0, {key: expand(value["value"]) for key, value in spec["outputs"].items()}


def fixture(work: Path, files: dict[str, str]) -> str:
    work.mkdir()
    command(["git", "init", "-q"], work)
    command(["git", "config", "user.name", "Synthetic verifier"], work)
    command(["git", "config", "user.email", "fixture@example.invalid"], work)
    (work / "README.md").write_text("Synthetic consumer\n")
    command(["git", "add", "."], work)
    command(["git", "commit", "-qm", "base"], work)
    base = command(["git", "rev-parse", "HEAD"], work)
    for name, body in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    command(["git", "add", "."], work)
    command(["git", "commit", "-qm", "fixture", "--allow-empty"], work)
    return base


def verify(evidence: Path) -> None:
    server = ApiServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="estate-consumers-", dir=evidence) as tmp:
            scratch = Path(tmp)
            common = {"src/cost.ts": "export const cost = 1;\n",
                      "tests/cost.test.ts": 'import "../src/cost";\n'}
            cases: list[tuple[str, dict[str, str], str, int, dict[str, str],
                              list[dict[str, str]], str, list[str]]] = [
                ("docs", {"docs/guide.md": "Guide\n"}, "docs/guide.md", 1, {}, [], "none", []),
                ("small", common, "src/cost.ts", 1, {}, [], "core", ["tests/cost.test.ts"]),
                ("unmatched-source", {"src/new.ts": "export const value = 1;\n"},
                 "src/new.ts", 1, {}, [], "core", []),
                ("convex", {"convex/feedback.ts": "export const submit = 1;\n",
                            "tests/access.test.ts": "t.mutation(api.feedback.submit, {});\n"},
                 "convex/feedback.ts", 1, {}, [], "core", ["tests/access.test.ts"]),
                ("unmatched", {"convex/auth.ts": "export const check = 1;\n"},
                 "convex/auth.ts", 1, {}, [], "standard", []),
                ("large", common, "src/cost.ts", 51, {}, [], "standard", []),
                ("full-path", common, "src/cost.ts", 1, {"full-paths": "src/**"}, [], "standard", []),
                ("db-test", {"workers/test_cost_postgres.py": "def test_cost():\n    pass\n"},
                 "workers/test_cost_postgres.py", 1, {}, [], "standard", []),
                ("full-label", {"docs/guide.md": "Guide\n"}, "docs/guide.md", 1, {},
                 [{"name": "ci:full"}], "standard", []),
                ("agent-skill-markdown", {
                    ".agents/skills/verify-dissidium/SKILL.md": "Verify\n"},
                 ".agents/skills/verify-dissidium/SKILL.md", 80, {}, [], "standard", []),
                ("docs-include", {
                    ".agents/skills/verify-dissidium/SKILL.md": "Verify\n",
                    "scripts/other.sh": "echo ok\n"},
                 ".agents/skills/verify-dissidium/SKILL.md", 80,
                 {"docs-include": ".agents/skills/verify-dissidium/**"}, [], "none", []),
                ("docs-include-product", {"src/README.md": "Product\n"}, "src/README.md", 80,
                 {"docs-include": "**"}, [], "standard", []),
                ("docs-include-read", {
                    ".agents/skills/verify-dissidium/SKILL.md": "Verify\n",
                    "scripts/read.sh": "cat .agents/skills/verify-dissidium/SKILL.md\n"},
                 ".agents/skills/verify-dissidium/SKILL.md", 4,
                 {"docs-include": ".agents/skills/verify-dissidium/**"}, [], "core", []),
            ]
            for name, files, changed, lines, inputs, labels, want, tests in cases:
                dest = evidence / name
                dest.mkdir()
                work = scratch / name
                fixture(work, files)
                event = dest / "event.json"
                event.write_text(json.dumps({"pull_request": {"number": 3, "labels": labels},
                                             "repository": {"default_branch": "main"}}))
                server.routes = {"pulls/3/files": [{"filename": changed, "additions": lines,
                                                    "deletions": 0, "status": "modified"}],
                                 f"git/commits/{TREE}": {"tree": {"sha": TREE}}}
                env = {**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": TREE,
                       "GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": REPO,
                       "GITHUB_API_URL": f"http://127.0.0.1:{server.server_port}",
                       "GITHUB_WORKSPACE": str(work), "RUNNER_TEMP": str(dest),
                       "GITHUB_STEP_SUMMARY": str(dest / "summary.md")}
                code, outputs = action(ROOT / "tier/action.yml", work, dest, env, inputs)
                assert code == 0 and outputs["tier"] == want, (name, code, outputs)
                assert json.loads(outputs["direct-tests"]) == tests, (name, outputs)
                results.append({"journey": name, "outputs": outputs})

            for name in ("tested-push", "merge-skew", "api-failure", "dispatch"):
                dest = evidence / name
                dest.mkdir()
                work = scratch / name
                fixture(work, common)
                event = dest / "event.json"
                event.write_text(json.dumps({"ref": "refs/heads/main", "before": "b" * 40,
                    "after": "a" * 40, "repository": {"default_branch": "main"},
                    "pull_request": {"number": 3, "labels": []}}))
                server.routes = {
                    "commits/" + "a" * 40 + "/pulls": [{"number": 7, "merged_at": "now",
                        "merge_commit_sha": "a" * 40, "head": {"sha": "h" * 40}}],
                    "actions/workflows/ci.yml/runs": {"workflow_runs": [{"id": 99}]},
                    "actions/runs/99/jobs": {"jobs": [{"name": "ci tier", "id": 5}]},
                    "check-runs/5/annotations": [{"title": "ci-tier", "message": "tested-tree=" + TREE}],
                    "git/commits/" + "a" * 40: {"tree": {"sha": TREE if name == "tested-push" else "2" * 40}},
                    "compare/" + "b" * 40 + "..." + "a" * 40: {"files": [
                        {"filename": "src/cost.ts", "additions": 51, "deletions": 0}]},
                }
                env = {**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch" if name == "dispatch" else
                       "pull_request" if name == "api-failure" else "push", "GITHUB_SHA": TREE,
                       "GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": REPO,
                       "GITHUB_API_URL": f"http://127.0.0.1:{server.server_port}",
                       "GITHUB_WORKFLOW_REF": "fixture/consumer/.github/workflows/ci.yml@refs/heads/main",
                       "GITHUB_WORKSPACE": str(work), "RUNNER_TEMP": str(dest),
                       "GITHUB_STEP_SUMMARY": str(dest / "summary.md")}
                code, outputs = action(ROOT / "tier/action.yml", work, dest, env)
                assert code == 0 and outputs["tier"] == ("none" if name == "tested-push" else "standard"), outputs
                results.append({"journey": name, "outputs": outputs})

            for name, bad, strict in [("bounded", False, False), ("warn", True, False),
                                       ("strict", True, True), ("unavailable", False, False),
                                       ("strict-unavailable", False, True),
                                       ("excluded-server", True, False), ("skip-workflows", True, False)]:
                dest = evidence / name
                dest.mkdir()
                work = scratch / name
                files = {"server/calls.ts": 'fetch("https://example.invalid", '
                         + ('{}' if bad else '{signal: AbortSignal.timeout(1000)}') + ');\n',
                         "server/calls.py": 'import requests\nrequests.get("https://example.invalid"'
                         + (')\n' if bad else ', timeout=1)\n'),
                         "calls.sh": 'curl ' + ('' if bad else '--max-time 1 ')
                         + 'https://example.invalid\n',
                         ".github/workflows/ci.yml": ('on: push\njobs:\n  check:\n'
                         '    runs-on: ubuntu-latest\n    steps:\n'
                         '      - run: curl https://example.invalid\n' if bad else
                         'on: pull_request\nconcurrency: ci\njobs:\n  check:\n'
                         '    runs-on: ${{ fromJSON(vars.CI_RUNNER) }}\n'
                         '    timeout-minutes: 10\n    steps:\n      - run: echo ok\n')}
                base = fixture(work, files)
                event = dest / "event.json"
                event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}))
                env = {**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event),
                       "GITHUB_WORKSPACE": str(work), "RUNNER_TEMP": str(dest),
                       "GITHUB_STEP_SUMMARY": str(dest / "summary.md"),
                       "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0"}
                inputs = {"fail-on-findings": str(strict).lower()}
                if name in ("unavailable", "strict-unavailable"):
                    inputs["rules-path"] = str(dest / "missing.yml")
                if name == "excluded-server":
                    inputs["exclude-paths"] = "server/**"
                if name == "skip-workflows":
                    inputs["check-workflows"] = "false"
                code, outputs = action(ROOT / "action.yml", work, dest, env, inputs)
                logs = "\n".join(p.read_text() for p in dest.glob("*-log.txt"))
                if strict:
                    expected = "Estate check could not run" if name == "strict-unavailable" else "unbounded outside call(s) detected"
                    assert code != 0 and expected in logs, logs
                elif name == "unavailable":
                    assert code == 0 and "Estate check could not run" in logs, logs
                else:
                    assert code == 0, logs
                    assert int(outputs["findings-count"]) == (2 if name == "excluded-server" else 4 if bad else 0), outputs
                    assert int(outputs["workflow-warnings-count"]) == (0 if name == "skip-workflows" else 4 if bad else 0), outputs
                    if name == "excluded-server":
                        assert "exclude-paths hides server code" in logs, logs
                    elif bad:
                        for rule in ("fetch-no-signal", "python-requests-no-timeout",
                                     "shell-curl-no-timeout", "workflow-curl-no-timeout"):
                            assert rule in logs, (rule, logs)
                results.append({"journey": name, "exit": code, "outputs": outputs})

            # Install-timeout gate (tracker t4456): an unbounded package install
            # fails the run by default; a bounded one passes; the opt-out skips.
            install_workflow = ("on: pull_request\nconcurrency: ci\njobs:\n  check:\n"
                                "    runs-on: ${{ fromJSON(vars.CI_RUNNER) }}\n    steps:\n"
                                "      - name: Install tools\n")
            bounded_step = "        timeout-minutes: 3\n"
            install_step = ("        run: |\n          set -euo pipefail\n"
                            "          pip install --quiet requests\n          npm ci\n")
            for name, bounded, skip in [("install-unbounded", False, False),
                                        ("install-bounded", True, False),
                                        ("install-skip", False, True)]:
                dest = evidence / name
                dest.mkdir()
                work = scratch / name
                step = bounded_step + install_step if bounded else install_step
                files = {"src/cost.ts": "export const cost = 1;\n",
                         ".github/workflows/ci.yml": install_workflow + step}
                base = fixture(work, files)
                event = dest / "event.json"
                event.write_text(json.dumps({"pull_request": {"base": {"sha": base}}}))
                env = {**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event),
                       "GITHUB_WORKSPACE": str(work), "RUNNER_TEMP": str(dest),
                       "GITHUB_STEP_SUMMARY": str(dest / "summary.md"),
                       "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0"}
                inputs = {"check-installs": "false"} if skip else {}
                code, outputs = action(ROOT / "action.yml", work, dest, env, inputs)
                logs = "\n".join(p.read_text() for p in dest.glob("*-log.txt"))
                if name == "install-unbounded":
                    assert code != 0, logs
                    assert "installs packages with no timeout" in logs, logs
                    assert "install-timeout-count=2" in logs, logs
                else:
                    assert code == 0, logs
                    assert int(outputs["install-timeout-count"]) == 0, outputs
                    if skip:
                        assert "Install timeout check skipped" in logs, logs
                results.append({"journey": name, "exit": code, "outputs": outputs})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), "Fixture API thread did not stop"
    (evidence / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    assert not list(evidence.glob("estate-consumers-*")), "Fixture checkouts survived cleanup"
    print(f"Verified {len(results)} action journeys; consumers/API cleaned; evidence: {evidence}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    assert shutil.which("semgrep"), "Launch with the skill's pinned Semgrep environment"
    expected = yaml.safe_load((ROOT / "action.yml").read_text())["inputs"]["semgrep-version"]["default"]
    actual = command(["semgrep", "--version"], ROOT)
    assert actual == expected, f"Expected Semgrep {expected}, got {actual}; use the skill launch command"
    (evidence / "doctor.json").write_text(json.dumps({
        "source_sha": command(["git", "rev-parse", "HEAD"], ROOT),
        "working_changes": command(["git", "status", "--porcelain"], ROOT),
        "semgrep": actual,
        "sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [ROOT / "action.yml", ROOT / "tier/action.yml", ROOT / "tier/classify.py",
                             ROOT / "scripts/generate-summary.py", ROOT / "scripts/workflow-standards.py",
                             ROOT / "scripts/install-timeouts.py"]},
    }, indent=2) + "\n")
    verify(evidence)


if __name__ == "__main__":
    main()
