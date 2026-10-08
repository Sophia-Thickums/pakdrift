# pakdrift

**After your game patches, does your mod still point at things that exist — and are those things still the same things?**

`pakdrift` answers two questions about an Unreal Engine 5 IoStore mod (`~mods/Name/*.utoc`)
against the game you actually have installed.

1. **Name-level** — does every package the mod ships still exist in the game, or is it now
   aimed at packages the patch renamed or removed?

       pakdrift --build-index --game '<game>/Client/.../Content/Paks'
       pakdrift --mod '<...>/Paks/~mods/SomeMod' --game '<...>/Paks'

2. **Content-level** — a package can *keep its name* and still change its internals. The mod
   then loads and renders wrong, with **no name anywhere missing**. Snapshot the game before a
   patch, compare after:

       pakdrift --snapshot '<...>/Paks' --out pre-patch.json     # BEFORE the patch
       pakdrift --compare-snapshot pre-patch.json '<...>/Paks'   # AFTER the patch

   The comparison is on each package's **content fingerprint** (its IoHash, read from the
   IoStore chunk id, which is content-derived) — so a changed package is caught even though its
   name still resolves.

## The three verdicts — and the third is the point

    exit 0   RESOLVES   every package the mod ships exists (name-level), or nothing changed
                        vs the snapshot (content-level)
    exit 1   DRIFT      at least one target is gone, or a package changed bytes / vanished
    exit 2   UNKNOWN    an input could not be read

**UNKNOWN is never reported as clean.** Most tools in this space verify their own work
internally and report success; this one refuses to answer at all when it cannot read its
inputs, and says which of the three it did. A check that cannot fail is not a check.

It also draws the honest line on what RESOLVES means: *every target is present* (name-level) or
*nothing changed vs the snapshot* (content-level). Neither is proof the game accepts the mod —
nothing read from a file can be.

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
| game package index | **250,092 packages** (names **and** content hashes) built in **5.2 s** |
| all 27 installed mods | every one `RESOLVES` (exit 0), assets ranging 3–40 per mod |
| **name-level negative control** | an index from the *shader chunk only* (7,558 packages); a real mod then reports **DRIFT, exit 1**, naming all five of its orphaned packages |
| **content-level, unchanged** | snapshot vs the same game → **RESOLVES, exit 0** |
| **content-level, changed** | one package keeps its name, its bytes change → **DRIFT, exit 1, naming it** — while the **name-level** check on the *same package* still says RESOLVES |
| content-level, removed | a package present in the snapshot is gone now → **DRIFT, exit 1** |
| unknown control | a missing snapshot / unreadable container → **exit 2**, never 0 |
| self-test | `pakdrift --self-test` — **8/8**, including cases that must go red |

**The negative controls matter more than the passes.** The content-level pair (unchanged vs
changed) is the whole reason v3 exists: it proves the tool can see drift the name-level compare
is blind to, and that it does not cry wolf when nothing moved.

## The honest limits

- **Content-level drift needs a before/after pair.** It is only detectable against a snapshot
  taken *before* the patch. If you never took one, `--compare-snapshot` cannot invent it — take
  a snapshot today and the *next* patch is covered.
- The content fingerprint is the package's IoHash. Under the container version here
  (`ReplaceIoChunkHashWithIoHash`) that is the content hash; on an older container version it may
  be name-derived, in which case the content-level compare degrades to no-changes rather than a
  false positive — a known bound, not a silent one.
- `retoc`'s asset-registry parse **panics on NTE's containers** (a name-map assertion; a retoc
  0.1.5 / UE 5.6 version gap, not this tool's bug). This tool uses `retoc manifest`, which works.
- `global.utoc` is skipped by name: it is the script-object store and holds no game packages.

Read-only. It does not modify the game, the mods, or anything else.

MIT.
