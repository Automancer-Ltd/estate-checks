#!/usr/bin/env python3
"""Waseem-authority merge gate (17 Oct 2026).

Refuses a PR that reverts, disables or flips config/flags/env set by a PR that Waseemilyas
authored or merged in the last WINDOW_DAYS, unless the PR names a card he approved
(`Waseem-approved: d123`) AND that card is in the approvals ledger for this repo.
Origin: s1359 reverted Rochdale #383 (his Stripe-live merge) via #389 on a Gmail DRAFT;
he ordered it undone (#390).

Usage: guard.py check --repo ORG/REPO --pr N [--window-days 30]
Exit 0 = allowed, 1 = REFUSED, 2 = could not decide (fails closed: truncated lists, missing
patches, unreachable ledger, any unexpected error).
Stdlib only; talks to GitHub through the gh CLI.

Ledger: waseem-guard/approvals.tsv on main of Automancer-Ltd/estate-checks (public), one line per approval:
  <card id>\t<repo ORG/REPO>\t<pr number or *>\t<date>\t<his answer, short, no secrets>
Only a change to that file approves an override, so the approval is a reviewed commit, not a string in the PR.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import re
import subprocess
import sys

WASEEM = "Waseemilyas"
WINDOW_DAYS = 30
LEDGER_API = "repos/Automancer-Ltd/estate-checks/contents/waseem-guard/approvals.tsv"
MAX_PAGES = 20

# Config-like files, matched on the basename or an anchored path, never on a substring.
CONFIG_BASENAMES = (r"wrangler[\w.\-]*\.jsonc?", r"\.env(\.[\w.\-]+)?", r"[\w.\-]+\.env", r"[\w.\-]+\.toml", r"[\w.\-]+\.ya?ml",
                    r"[\w.\-]+\.tsv", r"[\w.\-]+\.conf", r"deploy[\w.\-]*\.json", r"config(\.[A-Za-z0-9]+)?", r"Caddyfile", r"[\w.\-]+\.ini",
                    r"[\w.\-]+\.service", r"[\w.\-]+\.timer")
# Lockfiles, CI workflows and dependency manifests repeat lines everywhere; they are not settings.
NOT_CONFIG = (r"(^|/)[^/]*lock[^/]*$", r"(^|/)\.github/workflows/", r"(^|/)(Cargo|pyproject)\.toml$", r"(^|/)pnpm-workspace\.yaml$")
# Outside config files a line is a flag when it is ENV_STYLE or a name set to a boolean/null/0/1.
FLAGLINE = re.compile(r"^(?:export\s+)?(?:(?:const|let|var)\s+)?[\"']?(?:[A-Z][A-Z0-9_]{2,}|[A-Za-z_][\w.\-]*)[\"']?\s*[=:]\s*(?:true|false|null|0|1)\s*[,;]?$|^[\"']?[A-Z][A-Z0-9_]{2,}[\"']?\s*[=:]\s*\S")
KEYVAL = re.compile(r"^(?:export\s+)?(?:(?:const|let|var)\s+)?[\"']?([A-Za-z_][\w.\-]*)[\"']?\s*[=:]\s*(.+?)\s*,?$")
APPROVAL = re.compile(r"Waseem-approved:\s*(?:https?://\S*/)?([dtnc]\d{1,6})\b", re.I)


class Undecided(Exception):
    pass


def is_config_file(path):
    if any(re.search(g, path) for g in NOT_CONFIG):
        return False
    base = path.rsplit("/", 1)[-1]
    return any(re.fullmatch(g, base) for g in CONFIG_BASENAMES)


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


def keyval(line):
    m = KEYVAL.match(line)
    return (m.group(1), m.group(2).rstrip(",;")) if m else None


def config_overlap(pr_files, waseem_prs):
    """A setting he added that this PR deletes (removed line) or overrides (the same key set to another value)."""
    hits = []
    for pf in pr_files:
        if "patch" not in pf and not pf.get("changes") == 0:
            for wp in waseem_prs:
                if any(wf.get("filename") in (pf.get("filename"), pf.get("previous_filename")) for wf in wp.get("files", [])):
                    raise Undecided("no patch for %s (too large or binary) and it was changed by his #%d" % (pf.get("filename"), wp["number"]))
        removed = [x for x in patch_lines(pf.get("patch"), "-") if substantive(x)]
        added_here = [x for x in patch_lines(pf.get("patch"), "+") if substantive(x)]
        if not removed and not added_here:
            continue
        flaggy = is_config_file(pf["filename"])
        for wp in waseem_prs:
            for wf in wp.get("files", []):
                if wf.get("filename") not in (pf.get("filename"), pf.get("previous_filename")):
                    continue
                hers = [x for x in patch_lines(wf.get("patch"), "+") if substantive(x)]
                hset = set(hers)
                for line in removed:
                    if line in hset and (flaggy or FLAGLINE.match(line)):
                        hits.append({"file": pf["filename"], "line": line[:120], "pr": wp["number"], "title": wp.get("title", ""), "how": "removes"})
                his_kv = {}
                for line in hers:
                    kv = keyval(line)
                    if kv and (flaggy or FLAGLINE.match(line)):
                        his_kv[kv[0]] = kv[1]
                for line in added_here:
                    kv = keyval(line)
                    if kv and kv[0] in his_kv and kv[1] != his_kv[kv[0]] and (flaggy or FLAGLINE.match(line)):
                        hits.append({"file": pf["filename"], "line": line[:120], "pr": wp["number"], "title": wp.get("title", ""), "how": "overrides his %s" % kv[0]})
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


def ledger_entries(text):
    out = []
    for ln in (text or "").splitlines():
        parts = ln.split("\t")
        if len(parts) >= 3 and re.fullmatch(r"[dtnc]\d{1,6}", parts[0].strip().lower()):
            out.append((parts[0].strip().lower(), parts[1].strip().lower(), parts[2].strip()))
    return out


def approved(ident, repo, pr_number, ledger_text):
    """The card is approved for THIS repo and this PR (or `*`). An id from another repo's approval does not replay."""
    return any(i == ident and r == repo.lower() and p in ("*", str(pr_number)) for i, r, p in ledger_entries(ledger_text))


def find_approval(texts):
    for t in texts:
        m = APPROVAL.search(t or "")
        if m:
            return m.group(1).lower()
    return None


def refusal(revert_hits, cfg_hits):
    prs = {}
    for p in revert_hits:
        prs[p["number"]] = p.get("title", "")
    for h in cfg_hits:
        prs[h["pr"]] = h["title"]
    named = ", ".join("#%d (%s)" % (n, t) for n, t in sorted(prs.items()))
    out = ["REFUSED: this PR reverts, disables or flips a setting that Waseem authored or merged in the last %d days: %s." % (WINDOW_DAYS, named),
           "Nothing on this estate happens by accident; his changes are his decision (skill waseem-authored-changes).",
           "File ONE tracker card asking him (e.g. \"Did you mean to ...?\"). Once he approves it, add its line to waseem-guard/approvals.tsv in estate-checks and put `Waseem-approved: <card id>` in the PR body.",
           "Do not call his change a mistake. Check his SENT mail (not drafts) and his active bb threads first."]
    for h in cfg_hits[:5]:
        out.append("  - %s: %s `%s` added in #%d" % (h["file"], h.get("how", "removes"), h["line"], h["pr"]))
    return "\n".join(out)


def decide(pr, pr_files, commits, waseem_prs, approval_texts, ledger_text=None):
    """Pure decision. -> (allowed, message). ledger_text None = ledger unreachable (Undecided if an approval is claimed)."""
    others = [p for p in waseem_prs if p["number"] != pr["number"]]
    rv = detect_revert(pr.get("title", ""), pr.get("body", ""), commits, others)
    cf = config_overlap(pr_files, others)
    if not rv and not cf:
        return True, "waseem-guard: no conflict with his changes in the last %d days" % WINDOW_DAYS
    ident = find_approval(approval_texts)
    if ident is None:
        return False, refusal(rv, cf)
    if ledger_text is None:
        raise Undecided("%s is named but the approvals ledger could not be read" % ident)
    repo = pr["base"]["repo"]["full_name"]
    if approved(ident, repo, pr["number"], ledger_text):
        return True, "waseem-guard: conflict with his change, approved by %s (in the approvals ledger for %s)" % (ident, repo)
    return False, refusal(rv, cf) + "\n%s is not in waseem-guard/approvals.tsv for %s #%d." % (ident, repo, pr["number"])


def gh(path):
    try:
        r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise Undecided("gh api %s timed out" % path)
    if r.returncode != 0:
        raise Undecided("gh api %s: %s" % (path, r.stderr.strip()[:200]))
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Undecided("gh api %s: not JSON" % path)


def pages(path, limit=MAX_PAGES, stop=None):
    """Every page, or Undecided: a list cut at the page limit is never a decision. `stop(chunk)` ends early when the answer is complete."""
    out = []
    sep = "&" if "?" in path else "?"
    for i in range(1, limit + 1):
        chunk = gh("%s%sper_page=100&page=%d" % (path, sep, i))
        out += chunk
        if len(chunk) < 100 or (stop and stop(chunk)):
            return out
    raise Undecided("%s has more than %d pages; cannot be sure nothing was missed" % (path, limit))


def load_waseem_prs(repo, days, now=None):
    since = (now or datetime.datetime.now(datetime.timezone.utc)) - datetime.timedelta(days=days)

    def when(s):
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    # Sorted by updated: a PR merged inside the window was updated at or after its merge, so a page whose
    # every PR was last updated before the window ends the walk with nothing missed.
    closed = pages("repos/%s/pulls?state=closed&sort=updated&direction=desc" % repo, stop=lambda c: all(when(p["updated_at"]) < since for p in c))
    def mine(p):
        if not p.get("merged_at") or when(p["merged_at"]) < since:
            return None
        by = (p.get("user") or {}).get("login")
        merged_by = None
        if by != WASEEM:
            merged_by = (gh("repos/%s/pulls/%d" % (repo, p["number"])).get("merged_by") or {}).get("login")
        if WASEEM not in (by, merged_by):
            return None
        return {"number": p["number"], "title": p.get("title", ""),
                "shas": [s for s in (p.get("merge_commit_sha"), p["head"]["sha"]) if s],
                "files": pages("repos/%s/pulls/%d/files" % (repo, p["number"]))}
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = [r for r in ex.map(mine, closed) if r]
    return res


def read_ledger():
    r = gh(LEDGER_API)
    return base64.b64decode(r["content"]).decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    a = ap.parse_args(argv)
    try:
        pr = gh("repos/%s/pulls/%d" % (a.repo, a.pr))
        files = pages("repos/%s/pulls/%d/files" % (a.repo, a.pr))
        commits = [c["commit"]["message"] for c in pages("repos/%s/pulls/%d/commits" % (a.repo, a.pr))]
        comments = [c.get("body", "") for c in pages("repos/%s/issues/%d/comments" % (a.repo, a.pr))]
        wprs = load_waseem_prs(a.repo, a.window_days)
        texts = [pr.get("body", ""), pr.get("title", "")] + comments
        ledger = None
        if find_approval(texts):
            try:
                ledger = read_ledger()
            except Undecided:
                ledger = None
        ok, msg = decide(pr, files, commits, wprs, texts, ledger)
    except Undecided as e:
        print("waseem-guard: COULD NOT DECIDE (fails closed): %s" % e)
        return 2
    except Exception as e:  # noqa: BLE001 - any surprise is "could not decide", never a quiet pass
        print("waseem-guard: COULD NOT DECIDE (fails closed): unexpected %s: %s" % (type(e).__name__, e))
        return 2
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
