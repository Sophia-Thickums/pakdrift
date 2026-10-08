#!/usr/bin/env python3
"""
pakdrift — patch-drift detection for Unreal Engine IoStore PAK mods.  v3.

    pakdrift --mod '<game>/.../Paks/~mods/Name' --game '<game>/.../Paks'
    pakdrift --build-index --game '<Paks>'        # cache the game's package index
    pakdrift --snapshot '<Paks>' --out pre.json   # record package name+hash BEFORE a patch
    pakdrift --compare-snapshot pre.json '<Paks>' # AFTER a patch: what changed under you?
    pakdrift --self-test
    pakdrift --version

WHAT IT ANSWERS, in two questions:

  1. NAME-LEVEL (--mod/--game): after the game patches, does a mod still aim at packages
     that exist, or is it aimed at packages the game renamed or removed?

  2. CONTENT-LEVEL (--snapshot / --compare-snapshot): a package can KEEP its name and still
     change its internals — the mod then loads and renders wrong, with no name anywhere
     missing. This compares the package's CONTENT FINGERPRINT (its IoHash, read from the
     IoStore chunk id, which is content-derived) before and after a patch, so a package whose
     bytes changed under a mod is caught even though its name still resolves.

HOW IT READS A CONTAINER (the honest mechanism):
  It shells out to `retoc manifest <file.utoc>`, which parses the IoStore directory index and
  returns each entry's PACKAGE NAME plus its chunk id. The chunk id's leading 8 bytes are the
  package's IoHash (the container version here is `ReplaceIoChunkHashWithIoHash`, so the chunk
  id carries the content hash directly). NTE's containers are AES-encrypted, so `retoc` is
  called with the game's AES key (--aes-key, or $PAKDRIFT_AES_KEY).

THREE VERDICTS, and the third is the important one:
  exit 0  RESOLVES  every package the mod ships exists in the installed game (name-level),
                    or nothing changed vs the snapshot (content-level)
  exit 1  DRIFT     >=1 package the mod targets is gone (name-level), or >=1 package's
                    content hash changed / vanished vs the snapshot (content-level)
  exit 2  UNKNOWN   could not read an input. NEVER reported as clean, and never as drift.

SELF-TEST:  `--self-test` exercises both comparisons AND proves each can go red, on synthetic
inputs, with no game and no retoc required.

Read-only. It does not modify the game, the mods, or anything else.
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

VERSION = "3.0"
INDEX_CACHE = os.path.expanduser("~/.cache/pakdrift/game-index.json")


class Unreadable(Exception):
    """An input could not be read. Callers MUST surface this as UNKNOWN, never as clean."""


# ---------------------------------------------------------------- retoc

def find_retoc(explicit=None):
    if explicit:
        if os.path.isfile(explicit):
            return os.path.abspath(explicit)
        raise Unreadable(f"--retoc given but not a file: {explicit}")
    for cand in (
        os.environ.get("PAKDRIFT_RETOC"),
        os.path.expanduser("~/.local/bin/retoc"),
        shutil.which("retoc"),
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return os.path.abspath(cand)
    raise Unreadable(
        "retoc not found (set --retoc, $PAKDRIFT_RETOC, or install it). "
        "retoc is what reads the IoStore directory index; without it this tool cannot judge."
    )


def utoc_files(root):
    """The game's own containers. `global.utoc` is skipped BY NAME: it is the script-object
    store, its manifest makes retoc panic, and it holds no game packages to compare against."""
    if os.path.isfile(root):
        if not root.lower().endswith(".utoc"):
            raise Unreadable(f"not a .utoc: {root}")
        return [root]
    if not os.path.isdir(root):
        raise Unreadable(f"no such path: {root}")
    out = []
    for dirpath, _d, filenames in os.walk(root):
        # never descend into the mods folder when indexing the game
        if os.path.basename(dirpath) == "~mods":
            continue
        for fn in filenames:
            if fn.lower().endswith(".utoc") and fn.lower() != "global.utoc":
                out.append(os.path.join(dirpath, fn))
    if not out:
        raise Unreadable(f"no game .utoc under: {root}")
    return sorted(out)


def _chunk_hash(entry):
    """The content fingerprint of one package entry: the leading 8 bytes (16 hex chars) of its
    ExportBundleData chunk id, which under `ReplaceIoChunkHashWithIoHash` IS the package's
    IoHash — content-derived, so a changed package yields a changed fingerprint even though its
    NAME is unchanged. Falls back to the bulk-data chunk id if there is no export chunk."""
    pack = entry.get("packagedata") or []
    if pack and pack[0].get("id"):
        return pack[0]["id"][:16]
    bulk = entry.get("bulkdata") or []
    if bulk and bulk[0].get("id"):
        return bulk[0]["id"][:16]
    return None


def manifest_map(retoc, path, aes_key=None):
    """{package_name: content_hash} for every package inside one .utoc, via retoc manifest.
    Raises Unreadable on any failure — never returns a partial map silently."""
    tmpd = tempfile.mkdtemp(prefix="pakdrift-")
    try:
        cmd = [retoc]
        if aes_key:
            cmd += ["-a", aes_key]
        cmd += ["manifest", os.path.abspath(path)]
        proc = subprocess.run(cmd, cwd=tmpd, capture_output=True, text=True, timeout=900)
        out = tmpd + "/pakstore.json"
        if not os.path.isfile(out):
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = err[-1] if err else f"exit {proc.returncode}"
            raise Unreadable(f"{os.path.basename(path)}: retoc could not read it ({tail})")
        with open(out) as fh:
            data = json.load(fh)
        entries = data.get("oplog", {}).get("entries", [])
        out_map = {}
        for e in entries:
            n = e.get("packagestoreentry", {}).get("packagename")
            if not n:
                continue
            h = _chunk_hash(e)
            if h:
                out_map[n] = h
        if not out_map:
            raise Unreadable(f"{os.path.basename(path)}: manifest parsed but carried no package names")
        return out_map
    except subprocess.TimeoutExpired as exc:
        raise Unreadable(f"{os.path.basename(path)}: retoc timed out") from exc
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def collect_map(retoc, root, aes_key=None):
    """Merge every container under `root` into one {name: hash} map."""
    acc = {}
    for u in utoc_files(root):
        acc.update(manifest_map(retoc, u, aes_key))
    return acc


# ---------------------------------------------------------------- index (name-level)

def build_index(retoc, game, aes_key=None):
    m = collect_map(retoc, game, aes_key)
    os.makedirs(os.path.dirname(INDEX_CACHE), exist_ok=True)
    payload = {
        "tool": "pakdrift", "version": VERSION, "kind": "game-index",
        "game": os.path.abspath(game), "count": len(m),
        "packages": sorted(m.keys()),            # name-level (kept for v2 compatibility)
        "hashes": m,                             # content-level fingerprints
    }
    with open(INDEX_CACHE, "w") as fh:
        json.dump(payload, fh)
    return payload


def load_index(game):
    try:
        with open(INDEX_CACHE) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Unreadable(
            f"no usable game index at {INDEX_CACHE} ({exc}). "
            f"Build it first: pakdrift --build-index --game '<Paks>'"
        ) from exc
    if os.path.abspath(game) != data.get("game"):
        raise Unreadable(
            f"index was built for {data.get('game')}, not {os.path.abspath(game)} — "
            f"rebuild it rather than comparing against the wrong game."
        )
    return set(data["packages"])


# ---------------------------------------------------------------- snapshot (content-level)

def take_snapshot(retoc, root, aes_key=None):
    m = collect_map(retoc, root, aes_key)
    return {
        "tool": "pakdrift", "version": VERSION, "kind": "snapshot",
        "root": os.path.abspath(root),
        "taken": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(m), "hashes": m,
    }


def load_snapshot(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Unreadable(f"cannot read snapshot {path} ({exc})") from exc
    if not isinstance(data, dict) or data.get("kind") != "snapshot":
        raise Unreadable(f"{path} is not a pakdrift snapshot")
    if not data.get("hashes"):
        raise Unreadable(f"{path} carries no package hashes")
    return data


# ---------------------------------------------------------------- verdicts (pure, testable)

def verdict(mod_names, game_names):
    """NAME-LEVEL: does every package the mod ships still exist in the game?"""
    if not mod_names:
        raise Unreadable("mod ships no package names — refusing to call that clean")
    overrides = mod_names & game_names
    orphans = mod_names - game_names
    return {
        "mod": len(mod_names), "game": len(game_names),
        "overrides": sorted(overrides), "orphans": sorted(orphans),
        "clean": not orphans,
    }


def compare_hashes(before, after):
    """CONTENT-LEVEL: which packages changed bytes or vanished under a patch?

    `before` and `after` are {name: hash} maps. A package that kept its name but changed its
    hash is CHANGED; one that is gone is REMOVED. An added package is not drift (it cannot
    break a mod built against the old game)."""
    if not before:
        raise Unreadable("snapshot carries no packages — refusing to call that clean")
    if not after:
        raise Unreadable("current container carries no packages — refusing to call that clean")
    changed = sorted(n for n, h in before.items() if n in after and after[n] != h)
    removed = sorted(n for n in before if n not in after)
    added = sorted(n for n in after if n not in before)
    return {
        "before": len(before), "after": len(after),
        "changed": changed, "removed": removed, "added": added,
        "clean": not (changed or removed),
    }


def emit_name(res, mod_path):
    print(f"mod : {mod_path}")
    print()
    print(f"  packages the mod ships        {res['mod']}")
    print(f"  ... that exist in the game    {len(res['overrides'])}")
    print(f"  ... that the game no longer has  {len(res['orphans'])}")
    if res["orphans"]:
        print()
        for n in res["orphans"][:40]:
            print(f"    ORPHAN  {n}")
        if len(res["orphans"]) > 40:
            print(f"    ... and {len(res['orphans']) - 40} more")
        print()
        print("VERDICT: DRIFT — this mod targets at least one package the installed game does not ship.")
        return 1
    print()
    print("VERDICT: RESOLVES — every package this mod ships exists in the installed game.")
    print("         That is proof the TARGETS are present. It is not proof the game accepts the mod.")
    return 0


def emit_hashes(res, snap_path):
    print(f"snapshot : {snap_path}")
    print()
    print(f"  packages then                 {res['before']}")
    print(f"  packages now                  {res['after']}")
    print(f"  ... CONTENT CHANGED           {len(res['changed'])}")
    print(f"  ... REMOVED                   {len(res['removed'])}")
    print(f"  ... added (not drift)         {len(res['added'])}")
    if res["changed"] or res["removed"]:
        print()
        for n in res["changed"][:40]:
            print(f"    CHANGED  {n}")
        for n in res["removed"][:40]:
            print(f"    REMOVED  {n}")
        extra = len(res["changed"]) + len(res["removed"]) - min(len(res["changed"]), 40) - min(len(res["removed"]), 40)
        if extra > 0:
            print(f"    ... and {extra} more")
        print()
        print("VERDICT: DRIFT — a package kept its name but changed bytes (or vanished) under the patch.")
        print("         Any mod built against the old bytes may now load and render wrong.")
        return 1
    print()
    print("VERDICT: RESOLVES — no package changed bytes or vanished vs the snapshot.")
    return 0


# ---------------------------------------------------------------- self-test

def self_test():
    ok = True
    g = {"/Game/A/x", "/Game/A/y", "/Game/B/z"}

    # NAME-LEVEL: clean
    r = verdict({"/Game/A/x"}, g)
    if r["clean"] and r["orphans"] == []:
        print("pass: name-level clean case reports RESOLVES")
    else:
        print(f"FAIL: name-level clean -> {r}"); ok = False

    # NAME-LEVEL: drift names the orphan
    r = verdict({"/Game/A/x", "/Game/Gone/q"}, g)
    if not r["clean"] and r["orphans"] == ["/Game/Gone/q"]:
        print("pass: name-level drift names the orphan")
    else:
        print(f"FAIL: name-level drift -> {r}"); ok = False

    # empty mod is UNKNOWN, not clean
    try:
        verdict(set(), g); print("FAIL: empty mod did not fail shut"); ok = False
    except Unreadable:
        print("pass: empty mod is UNKNOWN, not clean")

    # CONTENT-LEVEL: a package that KEEPS its name but changes its hash MUST be caught.
    before = {"/Game/A/x": "aaaa1111aaaa1111", "/Game/B/z": "bbbb2222bbbb2222"}
    after_clean = dict(before)
    after_drift = {"/Game/A/x": "cccc3333cccc3333", "/Game/B/z": "bbbb2222bbbb2222"}
    rc = compare_hashes(before, after_clean)
    if rc["clean"]:
        print("pass: content-level unchanged reports RESOLVES")
    else:
        print(f"FAIL: content-level unchanged -> {rc}"); ok = False
    rd = compare_hashes(before, after_drift)
    if not rd["clean"] and rd["changed"] == ["/Game/A/x"] and rd["removed"] == []:
        print("pass: content-level catches a same-name hash change (the whole point of v3)")
    else:
        print(f"FAIL: content-level hash change -> {rd}"); ok = False

    # CONTENT-LEVEL: a removed package is drift, an added one is not.
    rrm = compare_hashes(before, {"/Game/A/x": "aaaa1111aaaa1111"})
    if not rrm["clean"] and rrm["removed"] == ["/Game/B/z"]:
        print("pass: content-level flags a removed package")
    else:
        print(f"FAIL: content-level removal -> {rrm}"); ok = False
    radd = compare_hashes(before, {**before, "/Game/New/n": "dddd4444dddd4444"})
    if radd["clean"] and radd["added"] == ["/Game/New/n"]:
        print("pass: content-level ignores an ADDED package (not drift)")
    else:
        print(f"FAIL: content-level added -> {radd}"); ok = False

    # a missing retoc must be UNKNOWN, not a crash and not a pass
    try:
        find_retoc("/nonexistent/retoc")
        print("FAIL: bad --retoc path was accepted"); ok = False
    except Unreadable:
        print("pass: a missing retoc is UNKNOWN, not a silent pass")

    print()
    print("SELF-TEST:", "OK" if ok else "FAILED")
    return 0 if ok else 2


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pakdrift",
        description="Does a UE IoStore mod still target packages the installed game ships? (read-only)",
    )
    ap.add_argument("--mod", help="a mod's .utoc, or its folder")
    ap.add_argument("--game", help="the game's Paks folder")
    ap.add_argument("--build-index", action="store_true", help="build/refresh the game package index")
    ap.add_argument("--snapshot", metavar="ROOT", help="record a container/root's package hashes")
    ap.add_argument("--compare-snapshot", metavar="SNAP.json",
                    help="compare a saved snapshot against the CURRENT container/root (positional ROOT)")
    ap.add_argument("--out", help="output file for --snapshot (default: <name>.snapshot.json)")
    ap.add_argument("--retoc", help="path to the retoc binary")
    ap.add_argument("--aes-key", default=os.environ.get("PAKDRIFT_AES_KEY"),
                    help="container AES key (or $PAKDRIFT_AES_KEY) — required for encrypted games")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--version", action="version", version=f"pakdrift {VERSION}")
    args, rest = ap.parse_known_args(argv)

    if args.self_test:
        return self_test()

    # ---- snapshot mode
    if args.snapshot:
        try:
            retoc = find_retoc(args.retoc)
            snap = take_snapshot(retoc, os.path.expanduser(args.snapshot), args.aes_key)
        except Unreadable as exc:
            print(f"UNKNOWN: {exc}")
            return 2
        out = args.out or (os.path.basename(snap["root"].rstrip("/")) or "game") + ".snapshot.json"
        with open(out, "w") as fh:
            json.dump(snap, fh, indent=1)
        print(f"snapshot: {snap['count']} packages from {snap['root']}")
        print(f"written : {os.path.abspath(out)}")
        print("keep this file; after the next patch run:")
        print(f"  pakdrift --compare-snapshot {os.path.basename(out)} '<Paks>'")
        return 0

    # ---- compare-snapshot mode (ROOT is the one positional arg)
    if args.compare_snapshot:
        root = args.game or (rest[0] if rest else None)
        if not root:
            ap.error("--compare-snapshot needs the current container/root (--game or a positional path)")
        try:
            before = load_snapshot(os.path.expanduser(args.compare_snapshot))
            retoc = find_retoc(args.retoc)
            after = collect_map(retoc, os.path.expanduser(root), args.aes_key)
            res = compare_hashes(before["hashes"], after)
        except Unreadable as exc:
            print(f"UNKNOWN: {exc}")
            print("VERDICT: cannot judge — refusing to report clean.")
            return 2
        return emit_hashes(res, args.compare_snapshot)

    # ---- build index
    if args.build_index:
        if not args.game:
            ap.error("--build-index needs --game")
        try:
            retoc = find_retoc(args.retoc)
            p = build_index(retoc, os.path.expanduser(args.game), args.aes_key)
        except Unreadable as exc:
            print(f"UNKNOWN: {exc}")
            return 2
        print(f"index built: {p['count']} packages from {p['game']}")
        print(f"cached at  : {INDEX_CACHE}")
        return 0

    # ---- name-level mod check
    if not (args.mod and args.game):
        ap.print_help()
        return 2
    try:
        retoc = find_retoc(args.retoc)
        game_names = load_index(os.path.expanduser(args.game))
        mod_map = collect_map(retoc, os.path.expanduser(args.mod), args.aes_key)
        res = verdict(set(mod_map.keys()), game_names)
    except Unreadable as exc:
        print(f"UNKNOWN: {exc}")
        print("VERDICT: cannot judge — refusing to report clean.")
        return 2
    return emit_name(res, args.mod)


if __name__ == "__main__":
    sys.exit(main())
