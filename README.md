# pakdrift

**After your game patches, does your mod still point at things that exist?**

`pakdrift` answers one question about an Unreal Engine 5 IoStore mod (`~mods/Name/*.utoc`)
against the game you actually have installed: **does every package the mod ships still exist
in the game, or is it now aimed at packages the patch renamed or removed?**

That is the common patch-drift failure, and it is usually invisible until the game crashes
or an outfit renders wrong.

    pakdrift --build-index --game '<game>/Client/.../Content/Paks'
    pakdrift --mod '<...>/Paks/~mods/SomeMod' --game '<...>/Paks'

There is also a snapshot mode that diffs the SET of packages before and after a patch, so a
package a patch *removed or added* is named even when you are not looking at one mod:

    pakdrift --snapshot '<...>/Paks' --out pre-patch.json     # BEFORE the patch
    pakdrift --compare-snapshot pre-patch.json '<...>/Paks'   # AFTER the patch

## The three verdicts — and the third is the point

    exit 0   RESOLVES   every package the mod ships exists in the installed game
    exit 1   DRIFT      at least one package the mod targets is gone (or vanished vs a snapshot)
    exit 2   UNKNOWN    an input could not be read

**UNKNOWN is never reported as clean.** Most tools in this space verify their own work
internally and report success; this one refuses to answer at all when it cannot read its
inputs, and says which of the three it did. A check that cannot fail is not a check.

It also draws the honest line on what RESOLVES means: *every target is present*. That is not
proof the game accepts the mod — nothing read from a file can be.

## ★ THE HONEST LIMIT — this is a NAME/EXISTENCE compare, NOT a content compare

An earlier version of this tool (v3, 2026-10-08) claimed a **content-level** mode: that a
package keeping its name but changing its bytes could be caught via the IoStore "chunk hash".
**That claim was measured and is FALSE, and this file is the correction.**

The identifier `retoc` exposes per package (`packagedata[].id`) is the **package chunk id**,
and its leading bytes are a hash **of the package NAME, not of its contents.** Measured proof:
the mod `DaffodildNude` ships a package whose chunk id is `e7a48aaee5ac67…` with **5,127,072
bytes**, while the installed game ships the **same id** with **1,122,463 bytes** — different
bytes, identical id. So a same-id/same-hash read is **guaranteed** for any package that keeps
its name, and can never see an internal change.

**Consequence:** `--snapshot` / `--compare-snapshot` catch packages a patch **added or removed**
(existence drift). They do **not** catch a package whose internals changed under you.

**The real content compare exists** and is heavier: extract each chunk's bytes (`retoc get`) and
compare their SHA-256. That is what a true content-level mode must do — extract, do not trust the
id. It is stated here as the upgrade path, not as a shipped feature, because calling the id-based
read "content-level" is exactly the green-answer-about-the-wrong-thing this tool argues against.

## What it needs

- **Python 3** (stdlib only, no pip install)
- **`retoc`** — reads the IoStore directory index. Get the Linux build from
  https://github.com/trumank/retoc/releases and point at it with `--retoc` or `$PAKDRIFT_RETOC`.
- **The game's AES key**, for encrypted containers: `--aes-key` or `$PAKDRIFT_AES_KEY`.
  NTE's key is published by the community (Ayakamods, credited there to Senku Aoki):
  `0x390B40DA3E0805AE7397DFA707E7227DBA06C35E95262E7FFF8F8E60CBC7A69C`

## Verified

Measured on a real install, 2026-10-07/08 (Neverness to Everness, UE 5.6.1, 27 mods installed):

| what | result |
|---|---|
| game package index | **250,092 packages** built in ~2–5 s from the six `pakchunk*.utoc` |
| all 27 installed mods | every one `RESOLVES` (exit 0), assets ranging 3–40 per mod |
| **negative control** | built an index from the *shader chunk only* (7,558 packages); a real mod then reported **DRIFT, exit 1**, naming all five of its orphaned packages |
| existence diff | a package in the snapshot that is gone now → **DRIFT, exit 1**; unchanged → **RESOLVES, exit 0** |
| unknown control | a missing path / unreadable container returns **exit 2**, never 0 |
| self-test | `pakdrift --self-test` — includes cases that must go red |

**The negative control matters more than the passes.** It proves the tool can say no on real
inputs, which is the only thing that makes the 27 passes mean anything.

## The honest limits

- It is a **name/existence** compare (see the correction above). It catches renamed/removed
  packages, and packages a patch added/removed. It does **not** compare asset internals.
- `retoc`'s asset-registry parse **panics on NTE's containers** (a name-map assertion; a retoc
  0.1.5 / UE 5.6 version gap, not this tool's bug). This tool uses `retoc manifest`, which works.
- `global.utoc` is skipped by name: it is the script-object store and holds no game packages.

Read-only. It does not modify the game, the mods, or anything else.

MIT.
