"""Run: python3 -m unittest discover -s waseem-guard"""
import unittest

from guard import config_overlap, decide, detect_revert, find_approval

STRIPE = {"number": 383, "title": "Rochdale: Stripe live", "shas": ["05c236e7aaaa"],
          "files": [{"filename": "wrangler.jsonc", "patch": "@@ -38,1 +38,1 @@\n-    \"PAYMENT_DRIVER\": \"fake\",\n+    \"PAYMENT_DRIVER\": \"stripe\","}]}
REVERT_PATCH = [{"filename": "wrangler.jsonc", "patch": "@@ -38,1 +38,1 @@\n-    \"PAYMENT_DRIVER\": \"stripe\",\n+    \"PAYMENT_DRIVER\": \"fake\","}]


class Gate(unittest.TestCase):
    def test_389_style_revert_is_refused_naming_his_pr(self):
        pr = {"number": 389, "title": "Return to test mode", "body": ""}
        ok, msg = decide(pr, REVERT_PATCH, [], [STRIPE], [""])
        self.assertFalse(ok)
        self.assertIn("#383", msg)
        self.assertIn("File ONE tracker card", msg)

    def test_github_revert_title_detected(self):
        pr = {"number": 390, "title": 'Revert "Rochdale: Stripe live (#383)"', "body": "Reverts Org/repo#383"}
        self.assertEqual([p["number"] for p in detect_revert(pr["title"], pr["body"], [], [STRIPE])], [383])

    def test_this_reverts_commit_sha(self):
        r = detect_revert("fix", "", ["This reverts commit 05c236e7aaaa1234."], [STRIPE])
        self.assertEqual([p["number"] for p in r], [383])

    def test_unrelated_pr_passes(self):
        pr = {"number": 400, "title": "Fix typo", "body": ""}
        ok, _ = decide(pr, [{"filename": "README.md", "patch": "-old words here\n+new words here"}], [], [STRIPE], [""])
        self.assertTrue(ok)

    def test_removing_his_non_config_source_line_is_not_a_flip(self):
        w = {"number": 5, "title": "x", "shas": [], "files": [{"filename": "a.ts", "patch": "+const total = compute(items);"}]}
        self.assertEqual(config_overlap([{"filename": "a.ts", "patch": "-const total = compute(items);"}], [w]), [])

    def test_flag_line_in_source_is_a_flip(self):
        w = {"number": 5, "title": "flag", "shas": [], "files": [{"filename": "a.ts", "patch": "+FEATURE_ON = true"}]}
        self.assertEqual(len(config_overlap([{"filename": "a.ts", "patch": "-FEATURE_ON = true"}], [w])), 1)

    def test_approval_unverified_vs_verified_vs_denied(self):
        pr = {"number": 389, "title": "Return to test mode", "body": "Waseem-approved: d391"}
        self.assertTrue(decide(pr, REVERT_PATCH, [], [STRIPE], [pr["body"]])[0])
        self.assertTrue(decide(pr, REVERT_PATCH, [], [STRIPE], [pr["body"]], lambda i: True)[0])
        ok, msg = decide(pr, REVERT_PATCH, [], [STRIPE], [pr["body"]], lambda i: False)
        self.assertFalse(ok)
        self.assertIn("d391", msg)

    def test_free_text_is_not_approval(self):
        self.assertEqual(find_approval(["Waseem said it was fine", "approved d391"]), (None, None))

    def test_own_pr_not_counted_against_itself(self):
        pr = {"number": 383, "title": "Rochdale: Stripe live", "body": ""}
        self.assertTrue(decide(pr, REVERT_PATCH, [], [STRIPE], [""])[0])

    def test_ordinary_prop_and_comment_edits_pass(self):
        w = {"number": 5, "title": "copy", "shas": [], "files": [{"filename": "p.tsx", "patch": "+updated=\"13 October 2026\"\n+// explains the thing here"}]}
        pf = [{"filename": "p.tsx", "patch": "-updated=\"13 October 2026\"\n-// explains the thing here"}]
        self.assertEqual(config_overlap(pf, [w]), [])

    def test_later_pr_cannot_be_the_target(self):
        pr = {"number": 100, "title": "x", "body": ""}
        later = dict(STRIPE, number=383)
        self.assertTrue(decide(pr, REVERT_PATCH, [], [later], [""])[0])


if __name__ == "__main__":
    unittest.main()
