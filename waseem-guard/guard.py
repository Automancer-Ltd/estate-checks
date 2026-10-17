#!/usr/bin/env python3
"""Waseem-authority merge gate (17 Oct 2026).

Refuses a PR that reverts, disables or flips config/flags/env set by a commit or PR
that Waseemilyas authored or merged in the last WINDOW_DAYS, unless the PR links a
tracker item Waseem approved. Origin: s1359 reverted Rochdale #383 (his Stripe-live merge)
via #389 on a Gmail DRAFT; he ordered it undone (#390).

Usage: guard.py check --repo ORG/REPO --pr N [--verify-cmd CMD] [--window-days 30]
Exit 0 = allowed, 1 = REFUSED, 2 = could not decide (fails closed).
Stdlib only; talks to GitHub through the gh CLI.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

WASEEM = "Waseemilyas"
WINDOW_DAYS = 30
# Files where a removed line is a setting, flag or env value.
CONFIG_GLOBS = (r"wrangler[^/]*\.jsonc?$", r"\.env[^/]*$", r"\.toml$", r"\.ya?ml$", r"\.tsv$", r"\.conf$",
                r"(^|/)deploy[^/]*\.json$", r"(^|/)config[^/]*$", r"(^|/)Caddyfile", r"\.ini$", r"\.service$", r"\.timer$")
# Outside config files only ENV_STYLE keys count: ordinary props and assignments are routine edits.
FLAGLINE = re.compile(r"^[\"']?[A-Z][A-Z0-9_]{2,}[\"']?\s*[=:]\s*\S")
APPROVAL = re.compile(r"Waseem-approved:\s*(?:https?://\S*/)?([dtnc]\d{1,6})\b", re.I)


class Undecided(Exception):
    pass


def is_config_file(path):
    return any(re.search(g, path) for g in CONFIG_GLOBS)


def patch_lines(patch, sign):
    out = []
    for ln in (patch or "").splitlines():
        if ln.startswith(sign) and not ln.startswith(sign * 3):
            out.append(ln[1:].strip())
    return out


def substantive(line):
    if re.match(r"(//|#|/\*|\*|--)", line):
        return False  # comments explain a setting; removing one flips nothing
    return len(re.sub(r"[^A-Za-z0-9]", "", line)) >= 4


def config_overlap(pr_files, waseem_prs):
    """Lines this PR removes that one of his PRs added, in config-like files or flag-shaped lines."""
    hits = []
    for pf in pr_files:
        removed = [x for x in patch_lines(pf.get("patch"), "-") if substantive(x)]
        if not removed:
            continue
        for wp in waseem_prs:
            for wf in wp.get("files", []):
                if wf.get("filename") not in (pf.get("filename"), pf.get("previous_filename")):
                    continue
                added = set(patch_lines(wf.get("patch"), "+"))
                for line in removed:
                    if line in added and (is_config_file(pf["filename"]) or FLAGLINE.match(line)):
                        hits.append({"file": pf["filename"], "line": line[:120], "pr": wp["number"], "title": wp.get("title", "")})
    return hits


def _strip_num(title):
    return re.sub(r"\s*\(#\d+\)\s*$", "", title or "").strip().lower()


def detect_revert(title, body, commit_messages, waseem_prs):
    """His PRs this one reverts: GitHub revert titles/bodies, `This reverts commit <sha>`, `Revert ... #N`."""
    text = (title or "") + "\n" + (body or "")
    found = {}
    by_num = {p["number"]: p for p in waseem_prs}
    is_revert = bool(re.match(r"\s*revert\b", title or "", re.I)) or bool(re.search(r"\breverts?\s+\S*#\d+", text, re.I))
    if is_revert:
        for m in re.finditer(r"#(\d+)", text):
            n = int(m.group(1))
            if n in by_num:
                found[n] = by_num[n]
        m = re.match(r"\s*revert\s+\"(.+)\"", title or "", re.I)
        if m:
            inner = _strip_num(m.group(1))
            for p in waseem_prs:
                if inner and _strip_num(p.get("title", "")) == inner:
                    found[p["number"]] = p
    for msg in commit_messages:
        for m in re.finditer(r"reverts commit ([0-9a-f]{7,40})", msg, re.I):
            sha = m.group(1).lower()
            for p in waseem_prs:
                if any(s.lower().startswith(sha) or sha.startswith(s.lower()) for s in p.get("shas", []) if s):
                    found[p["number"]] = p
    return list(found.values())


def find_approval(texts, verify=None):
    """-> (id or None, verified bool|None). verified None = no verifier available."""
    for t in texts:
        for m in APPROVAL.finditer(t or ""):
            ident = m.group(1).lower()
            return ident, (verify(ident) if verify else None)
    return None, None


def refusal(revert_hits, cfg_hits):
    prs = {}
    for p in revert_hits:
        prs[p["number"]] = p.get("title", "")
    for h in cfg_hits:
        prs[h["pr"]] = h["title"]
    named = ", ".join("#%d (%s)" % (n, t) for n, t in sorted(prs.items()))
    out = ["REFUSED: this PR reverts, disables or flips a setting that Waseem authored or merged in the last %d days: %s." % (WINDOW_DAYS, named),
           "Nothing on this estate happens by accident; his changes are his decision (skill waseem-authored-changes).",
           "File ONE tracker card asking him (e.g. \"Did you mean to ...?\"). Once he approves it, put `Waseem-approved: <card id>` in the PR body.",
           "Do not call his change a mistake. Check his SENT mail (not drafts) and his active bb threads first."]
    for h in cfg_hits[:5]:
        out.append("  - %s: removes `%s` added in #%d" % (h["file"], h["line"], h["pr"]))
    return "\n".join(out)


def decide(pr, pr_files, commits, waseem_prs, approval_texts, verify=None):
    """Pure decision. -> (allowed, message)."""
    others = [p for p in waseem_prs if p["number"] < pr["number"]]
    rv = detect_revert(pr.get("title", ""), pr.get("body", ""), commits, others)
    cf = config_overlap(pr_files, others)
    if not rv and not cf:
        return True, "waseem-guard: no conflict with his changes in the last %d days" % WINDOW_DAYS
    ident, verified = find_approval(approval_texts, verify)
    if ident and verified is True:
        return True, "waseem-guard: conflict with his change, approved by tracker item %s (verified)" % ident
    if ident and verified is None:
        return True, "waseem-guard: conflict with his change; approval %s named but NOT verified here (the merge wrapper verifies)" % ident
    if ident and verified is False:
        return False, refusal(rv, cf) + "\nThe item %s is not approved by Waseem." % ident
    return False, refusal(rv, cf)


def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise Undecided("gh api %s: %s" % (path, r.stderr.strip()[:200]))
    return json.loads(r.stdout)


def pages(path, limit=3):
    out = []
    sep = "&" if "?" in path else "?"
    for i in range(1, limit + 1):
        chunk = gh("%s%sper_page=100&page=%d" % (path, sep, i))
        out += chunk
        if len(chunk) < 100:
            break
    return out


def load_waseem_prs(repo, days, now=None):
    since = (now or datetime.datetime.now(datetime.timezone.utc)) - datetime.timedelta(days=days)
    res = []
    for p in pages("repos/%s/pulls?state=closed&sort=updated&direction=desc" % repo, 5):
        if not p.get("merged_at"):
            continue
        if datetime.datetime.fromisoformat(p["merged_at"].replace("Z", "+00:00")) < since:
            continue
        by = (p.get("user") or {}).get("login")
        merged_by = None
        if by != WASEEM:
            merged_by = (gh("repos/%s/pulls/%d" % (repo, p["number"])).get("merged_by") or {}).get("login")
        if WASEEM not in (by, merged_by):
            continue
        res.append({"number": p["number"], "title": p.get("title", ""),
                    "shas": [s for s in (p.get("merge_commit_sha"), p["head"]["sha"]) if s],
                    "files": pages("repos/%s/pulls/%d/files" % (repo, p["number"]))})
    return res


def make_verify(cmd):
    if not cmd:
        return None

    def verify(ident):
        r = subprocess.run(cmd.split() + [ident], capture_output=True, timeout=60)
        return r.returncode == 0
    return verify


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--verify-cmd", default=os.environ.get("WASEEM_GUARD_VERIFY_CMD", ""))
    a = ap.parse_args(argv)
    try:
        pr = gh("repos/%s/pulls/%d" % (a.repo, a.pr))
        files = pages("repos/%s/pulls/%d/files" % (a.repo, a.pr))
        commits = [c["commit"]["message"] for c in pages("repos/%s/pulls/%d/commits" % (a.repo, a.pr))]
        comments = [c.get("body", "") for c in pages("repos/%s/issues/%d/comments" % (a.repo, a.pr), 2)]
        wprs = load_waseem_prs(a.repo, a.window_days)
    except Undecided as e:
        print("waseem-guard: COULD NOT DECIDE (fails closed): %s" % e)
        return 2
    ok, msg = decide(pr, files, commits, wprs, [pr.get("body", ""), pr.get("title", "")] + comments, make_verify(a.verify_cmd))
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
