#!/usr/bin/env python3
"""
pakdrift — patch-drift detection for Unreal Engine IoStore PAK mods.  v2.

    pakdrift --mod '<game>/.../Paks/~mods/Name' --game '<game>/.../Paks'
    pakdrift --build-index --game '<Paks>'        # cache the game's package index
    pakdrift --self-test

WHAT IT ANSWERS: after the game patches, does a mod still aim at packages that exist,
or is it aimed at packages the game has renamed or removed?

HOW IT READS A CONTAINER (this is the honest mechanism):
  It shells out to `retoc manifest <file.utoc>`, which parses the IoStore directory index
  and returns each entry's PACKAGE NAME (e.g. /Game/Characters/Vehicle/Vehicle_010/...)
  plus the filenames it writes. NTE's containers are AES-encrypted, so `retoc` is called
  with the game's AES key (--aes-key, or $PAKDRIFT_AES_KEY).

THREE VERDICTS, and the third is the important one:
  exit 0  RESOLVES  every package the mod ships exists in the installed game
  exit 1  DRIFT     >=1 package the mod targets is gone from the game
  exit 2  UNKNOWN   could not read an input. NEVER reported as clean, and never as drift.

SELF-TEST:  `--self-test` exercises the comparison AND proves it can go red, on
synthetic inputs, with no game and no retoc required.

Read-only. It does not modify the game, the mods, or anything else.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

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


def manifest_names(retoc, path, aes_key=None):
    """Package names inside one .utoc, via retoc manifest. Raises Unreadable on any failure."""
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
        names = set()
        for e in entries:
            n = e.get("packagestoreentry", {}).get("packagename")
            if n:
                names.add(n)
        if not names:
            raise Unreadable(f"{os.path.basename(path)}: manifest parsed but carried no package names")
        return names
    except subprocess.TimeoutExpired as exc:
        raise Unreadable(f"{os.path.basename(path)}: retoc timed out") from exc
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def collect_names(retoc, root, aes_key=None):
    acc = set()
    for u in utoc_files(root):
        acc |= manifest_names(retoc, u, aes_key)
    return acc


# ---------------------------------------------------------------- index

def build_index(retoc, game, aes_key=None):
    names = collect_names(retoc, game, aes_key)
    os.makedirs(os.path.dirname(INDEX_CACHE), exist_ok=True)
    payload = {"game": os.path.abspath(game), "count": len(names), "packages": sorted(names)}
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


# ---------------------------------------------------------------- verdict

def verdict(mod_names, game_names):
    """Pure comparison, so the self-test can exercise it directly."""
    if not mod_names:
        raise Unreadable("mod ships no package names — refusing to call that clean")
    overrides = mod_names & game_names
    orphans = mod_names - game_names
    return {
        "mod": len(mod_names),
        "game": len(game_names),
        "overrides": sorted(overrides),
        "orphans": sorted(orphans),
        "clean": not orphans,
    }


def emit(res, mod_path):
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


# ---------------------------------------------------------------- self-test

def self_test():
    ok = True
    g = {"/Game/A/x", "/Game/A/y", "/Game/B/z"}

    r = verdict({"/Game/A/x"}, g)
    if r["clean"] and r["orphans"] == []:
        print("pass: clean case reports RESOLVES")
    else:
        print(f"FAIL: clean case -> {r}")
        ok = False

    r = verdict({"/Game/A/x", "/Game/Gone/q"}, g)
    if not r["clean"] and r["orphans"] == ["/Game/Gone/q"]:
        print("pass: drift case names the orphan")
    else:
        print(f"FAIL: drift case -> {r}")
        ok = False

    try:
        verdict(set(), g)
        print("FAIL: empty mod did not fail shut")
        ok = False
    except Unreadable:
        print("pass: empty mod is UNKNOWN, not clean")

    # retoc missing must be UNKNOWN, not a crash and not a pass
    try:
        find_retoc("/nonexistent/retoc")
        print("FAIL: bad --retoc path was accepted")
        ok = False
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
    ap.add_argument("--retoc", help="path to the retoc binary")
    ap.add_argument("--aes-key", default=os.environ.get("PAKDRIFT_AES_KEY"),
                    help="container AES key (or $PAKDRIFT_AES_KEY) — required for encrypted games")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
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
    if not (args.mod and args.game):
        ap.print_help()
        return 2

    try:
        retoc = find_retoc(args.retoc)
        game_names = load_index(os.path.expanduser(args.game))
        mod_names = collect_names(retoc, os.path.expanduser(args.mod), args.aes_key)
        res = verdict(mod_names, game_names)
    except Unreadable as exc:
        print(f"UNKNOWN: {exc}")
        print("VERDICT: cannot judge — refusing to report clean.")
        return 2
    return emit(res, args.mod)


if __name__ == "__main__":
    sys.exit(main())
