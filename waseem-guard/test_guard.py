"""Run: python3 waseem-guard/test_guard.py"""
import unittest

import os
import subprocess
import tempfile

import guard
from guard import Undecided, approved, config_overlap, decide, detect_revert, find_approval, git_diff, is_config_file, is_his, pages

REPO = "IQ-Mouldings/rochdale-self-storage"
STRIPE = {"number": 383, "title": "Rochdale: Stripe live", "shas": ["05c236e7aaaa"],
          "files": [{"filename": "wrangler.jsonc", "patch": "@@ -38,1 +38,1 @@\n-    \"PAYMENT_DRIVER\": \"fake\",\n+    \"PAYMENT_DRIVER\": \"stripe\","}]}
REVERT_PATCH = [{"filename": "wrangler.jsonc", "patch": "@@ -38,1 +38,1 @@\n-    \"PAYMENT_DRIVER\": \"stripe\",\n+    \"PAYMENT_DRIVER\": \"fake\","}]
LEDGER = "d391\t%s\t389\t2026-10-17\tApprove: revert it\nd500\tother/repo\t*\t2026-10-17\tx\n" % REPO


def pr(n, title="Return to test mode", body=""):
    return {"number": n, "title": title, "body": body, "base": {"repo": {"full_name": REPO}}}


class Gate(unittest.TestCase):
    def test_389_style_revert_is_refused_naming_his_pr(self):
        ok, msg = decide(pr(389), REVERT_PATCH, [], [STRIPE], [""])
        self.assertFalse(ok)
        self.assertIn("#383", msg)
        self.assertIn("File ONE tracker card", msg)

    def test_github_revert_title_detected(self):
        self.assertEqual([p["number"] for p in detect_revert('Revert "Rochdale: Stripe live (#383)"', "Reverts Org/repo#383", [], [STRIPE])], [383])

    def test_this_reverts_commit_sha(self):
        self.assertEqual([p["number"] for p in detect_revert("fix", "", ["This reverts commit 05c236e7aaaa1234."], [STRIPE])], [383])

    def test_unrelated_pr_passes(self):
        ok, _ = decide(pr(400, "Fix typo"), [{"filename": "README.md", "patch": "-old words here\n+new words here"}], [], [STRIPE], [""])
        self.assertTrue(ok)

    def test_lower_numbered_pr_cannot_slip_past(self):  # reviewer blocker 3
        self.assertFalse(decide(pr(100), REVERT_PATCH, [], [STRIPE], [""])[0])

    def test_added_override_is_a_flip(self):  # reviewer blocker 4: his line stays, a later assignment wins
        override = [{"filename": "wrangler.jsonc", "patch": "@@ -50,0 +51,1 @@\n+    \"PAYMENT_DRIVER\": \"fake\","}]
        ok, msg = decide(pr(401), override, [], [STRIPE], [""])
        self.assertFalse(ok)
        self.assertIn("overrides his PAYMENT_DRIVER", msg)

    def test_missing_patch_on_a_file_he_changed_is_undecided(self):
        with self.assertRaises(Undecided):
            decide(pr(401), [{"filename": "wrangler.jsonc", "changes": 900}], [], [STRIPE], [""])

    def test_null_patch_on_either_side_is_undecided(self):  # review 2 blocker 3
        with self.assertRaises(Undecided):
            decide(pr(401), [{"filename": "wrangler.jsonc", "patch": None, "changes": 4}], [], [STRIPE], [""])
        big = dict(STRIPE, files=[{"filename": "wrangler.jsonc", "changes": 9000}])
        with self.assertRaises(Undecided):
            decide(pr(401), REVERT_PATCH, [], [big], [""])

    def test_flag_words_not_substrings(self):  # review 3 blocker 2
        def hit(name, v="true"):
            w = {"number": 5, "title": "x", "shas": [], "files": [{"filename": "a.ts", "patch": "+export const %s = %s;" % (name, v)}]}
            return len(config_overlap([{"filename": "a.ts", "patch": "-export const %s = %s;" % (name, v)}], [w]))
        for flag in ("stripeLive", "StripeLive", "LiveMode", "FeatureEnabled", "dry_run", "kill-switch", "HTTPLive", "APIEnabled", "CIMode"):
            self.assertEqual(hit(flag), 1, flag)
        for plain in ("model", "modern", "delivered", "threshold", "placeholder", "allowance", "retries", "isOn", "isOff", "turnOn", "canRun", "shouldRun", "hasRun", "isDry", "toggle", "mySwitch", "run", "dry"):
            self.assertEqual(hit(plain), 0, plain)

    def test_plain_initialisers_and_yaml_collisions_are_routine(self):  # review 2 blocker 2
        w = {"number": 5, "title": "x", "shas": [], "files": [{"filename": "a.ts", "patch": "+const retries = 0;\n+const index = 1;"},
             {"filename": "docker-compose.yml", "patch": "+    name: web"}]}
        self.assertEqual(config_overlap([{"filename": "a.ts", "patch": "-const retries = 0;\n-const index = 1;"},
                                         {"filename": "docker-compose.yml", "patch": "+    name: worker"}], [w]), [])

    def test_lowercase_boolean_flag_in_source_is_a_flip(self):
        w = {"number": 5, "title": "flag", "shas": [], "files": [{"filename": "a.ts", "patch": "+export const stripeLive = true;"}]}
        self.assertEqual(len(config_overlap([{"filename": "a.ts", "patch": "-export const stripeLive = true;"}], [w])), 1)

    def test_ordinary_edits_pass(self):
        w = {"number": 5, "title": "copy", "shas": [], "files": [{"filename": "p.tsx", "patch": "+updated=\"13 October 2026\"\n+const total = compute(items);\n+// explains the thing here"}]}
        pf = [{"filename": "p.tsx", "patch": "-updated=\"13 October 2026\"\n-const total = compute(items);\n-// explains the thing here"}]
        self.assertEqual(config_overlap(pf, [w]), [])

    def test_lockfiles_workflows_and_lookalike_names_are_not_config(self):  # reviewer blocker 6
        for p in ("pnpm-lock.yaml", ".github/workflows/ci.yml", "Cargo.toml", "docs/config-notes.md", "src/configuration.ts", "src/foo.environment.ts", "Caddyfile.bak"):
            self.assertFalse(is_config_file(p), p)
        for p in ("apps/web/wrangler.jsonc", ".env.production", "deploy.json", "etc/Caddyfile", "settings/config.json", "data/x.tsv", "wrangler.toml"):
            self.assertTrue(is_config_file(p), p)

    def test_lockfile_line_he_added_does_not_block_a_bump(self):
        w = {"number": 5, "title": "dep", "shas": [], "files": [{"filename": "pnpm-lock.yaml", "patch": "+    version: 1.2.3"}]}
        self.assertEqual(config_overlap([{"filename": "pnpm-lock.yaml", "patch": "-    version: 1.2.3"}], [w]), [])


class Approval(unittest.TestCase):
    def test_marker_without_ledger_line_is_refused(self):  # reviewer blocker 1/2
        p = pr(389, body="Waseem-approved: d1")
        ok, msg = decide(p, REVERT_PATCH, [], [STRIPE], [p["body"]], LEDGER)
        self.assertFalse(ok)
        self.assertIn("not in waseem-guard/approvals.tsv", msg)

    def test_unreadable_ledger_is_undecided_not_allowed(self):
        p = pr(389, body="Waseem-approved: d391")
        with self.assertRaises(Undecided):
            decide(p, REVERT_PATCH, [], [STRIPE], [p["body"]], None)

    def test_ledger_line_for_this_repo_and_pr_allows(self):
        p = pr(389, body="Waseem-approved: d391")
        self.assertTrue(decide(p, REVERT_PATCH, [], [STRIPE], [p["body"]], LEDGER)[0])

    def test_id_does_not_replay_on_another_pr_or_repo(self):
        self.assertFalse(approved("d391", REPO, 400, LEDGER))
        self.assertFalse(approved("d500", REPO, 389, LEDGER))
        self.assertTrue(approved("d500", "OTHER/repo", 12, LEDGER))

    def test_free_text_is_not_approval(self):
        self.assertIsNone(find_approval(["Waseem said it was fine", "approved d391"]))

    def test_own_pr_not_counted_against_itself(self):
        self.assertTrue(decide(pr(383, "Rochdale: Stripe live"), REVERT_PATCH, [], [STRIPE], [""])[0])


class Completeness(unittest.TestCase):
    def test_a_list_cut_at_the_page_limit_is_undecided(self):
        import guard
        orig = guard.gh
        guard.gh = lambda path: [{"x": 1}] * 100
        try:
            with self.assertRaises(Undecided):
                pages("repos/o/r/pulls/1/files", limit=3)
        finally:
            guard.gh = orig


class HisOrHelper(unittest.TestCase):
    def test_branch_and_body_tell_his_from_a_helper(self):  # Waseem 17 Oct: #620 s1289/... is a helper PR
        for ref in ("ops/stripe-live", "revert-389-restore-stripe-live", "feature/checkout", "main-fix"):
            self.assertTrue(is_his(ref, "Flip PAYMENT_DRIVER."), ref)
        for ref in ("s1289/ungated-card-paths", "s1359/hold-live-payments", "ln-3fbe2832", "lane/x", "fix/cards", "devin/abc", "shape/cards", "dependabot/npm/x", "chair-thr_1/x"):
            self.assertFalse(is_his(ref, ""), ref)
        # His own interactive lane signs its PRs "Generated with Claude Code" (#383): that must still count as his.
        self.assertTrue(is_his("ops/stripe-live", "Undo: set it back.\n\n\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)"))
        self.assertFalse(is_his("tidy", "Opened by helper s1289 for the lane."))

    def test_helper_pr_is_not_loaded_and_its_files_are_never_fetched(self):
        calls = []
        merged = "2026-10-16T10:00:00Z"
        closed = [{"number": 620, "title": "ungated", "merged_at": merged, "updated_at": merged, "user": {"login": "Waseemilyas"}, "body": "", "head": {"ref": "s1289/ungated-card-paths", "sha": "a"}, "merge_commit_sha": "b"},
                  {"number": 383, "title": "live", "merged_at": merged, "updated_at": merged, "user": {"login": "Waseemilyas"}, "body": "", "head": {"ref": "ops/stripe-live", "sha": "c"}, "merge_commit_sha": "d"}]
        orig_pages, orig_gh = guard.pages, guard.gh

        def fake_pages(path, *a, **k):
            calls.append(path)
            return closed if "state=closed" in path else [{"filename": "wrangler.jsonc", "patch": "+x", "changes": 1}]
        guard.pages = fake_pages
        guard.gh = lambda path: {}
        try:
            import datetime
            got = guard.load_waseem_prs("o/r", 30, now=datetime.datetime(2026, 10, 17, tzinfo=datetime.timezone.utc))
        finally:
            guard.pages, guard.gh = orig_pages, orig_gh
        self.assertEqual([p["number"] for p in got], [383])
        self.assertFalse([c for c in calls if "/pulls/620/" in c])

    def test_helper_pr_with_a_huge_file_no_longer_blocks_a_pr(self):  # the #639 / #620 case
        pr_files = [{"filename": "src/app-next.html", "changes": 5000}]  # API gave no patch, git fills it in; his list has only real PRs of his
        pr_files[0]["patch"] = "-<div>old</div>\n+<div>new</div>"
        self.assertTrue(decide(pr(639, "chart block"), pr_files, [], [STRIPE], [""])[0])


def write(d, text):
    with open(os.path.join(d, "wrangler.jsonc"), "w") as fh:
        fh.write(text)


class GitFallback(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def git(*a):
            return subprocess.run(["git"] + list(a), cwd=self.d, env=env, check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q", "-b", "main")
        write(self.d, '{\n  "PAYMENT_DRIVER": "fake"\n}\n')
        git("add", "-A")
        git("commit", "-qm", "init")
        self.base = git("rev-parse", "HEAD")
        write(self.d, '{\n  "PAYMENT_DRIVER": "stripe"\n}\n')
        git("commit", "-qam", "his live flip")
        self.his = git("rev-parse", "HEAD")
        write(self.d, '{\n  "PAYMENT_DRIVER": "fake"\n}\n')
        git("commit", "-qam", "revert")
        self.rev = git("rev-parse", "HEAD")
        self._orig = guard.clone_for
        guard.clone_for = lambda repo: self.d

    def tearDown(self):
        guard.clone_for = self._orig

    def test_real_diff_when_the_api_gives_no_patch_catches_the_revert(self):
        mine_patch = git_diff("o/r", self.his + "^1", self.his, "wrangler.jsonc")
        pr_patch = git_diff("o/r", self.his, self.rev, "wrangler.jsonc")
        self.assertIn('+  "PAYMENT_DRIVER": "stripe"', mine_patch)
        his = {"number": 383, "title": "live", "shas": [self.his], "files": [{"filename": "wrangler.jsonc", "patch": mine_patch}]}
        hits = config_overlap([{"filename": "wrangler.jsonc", "patch": pr_patch}], [his])
        self.assertEqual({h["pr"] for h in hits}, {383})

    def test_unrelated_big_file_edit_passes_with_a_real_diff(self):
        his = {"number": 383, "title": "live", "shas": [], "files": [{"filename": "wrangler.jsonc", "patch": git_diff("o/r", self.his + "^1", self.his, "wrangler.jsonc")}]}
        self.assertEqual(config_overlap([{"filename": "wrangler.jsonc", "patch": '+  "OTHER_THING": "x"'}], [his]), [])

    def test_git_that_cannot_say_fails_closed(self):
        with self.assertRaises(Undecided):
            git_diff("o/r", "0" * 40, "1" * 40, "wrangler.jsonc")


if __name__ == "__main__":
    unittest.main()
