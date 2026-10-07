# pakdrift

**After your game patches, does your mod still point at things that exist?**

`pakdrift` answers one question about an Unreal Engine 5 IoStore mod (`~mods/Name/*.utoc`)
against the game you actually have installed: **does every package the mod ships still exist
in the game, or is it now aimed at packages the patch renamed or removed?**

That is the common patch-drift failure, and it is usually invisible until the game crashes
or an outfit renders wrong.

    pakdrift --build-index --game '<game>/Client/WindowsNoEditor/HT/Content/Paks'
    pakdrift --mod '<...>/Paks/~mods/SomeMod' --game '<...>/Paks'

## The three verdicts — and the third is the point

    exit 0   RESOLVES   every package the mod ships exists in the installed game
    exit 1   DRIFT      at least one package the mod targets is gone from the game
    exit 2   UNKNOWN    an input could not be read

**UNKNOWN is never reported as clean.** Most tools in this space verify their own work
internally and report success; this one refuses to answer at all when it cannot read its
inputs, and says which of the three it did. A check that cannot fail is not a check.

It also draws the honest line on what RESOLVES means: *every target is present*. That is not
proof the game accepts the mod — nothing read from a file can be.

## What it needs

- **Python 3** (stdlib only, no pip install)
- **`retoc`** — reads the IoStore directory index. Get the Linux build from
  https://github.com/trumank/retoc/releases and point at it with `--retoc` or `$PAKDRIFT_RETOC`.
- **The game's AES key**, for encrypted containers: `--aes-key` or `$PAKDRIFT_AES_KEY`.
  NTE's key is published by the community (Ayakamods, credited there to Senku Aoki):
  `0x390B40DA3E0805AE7397DFA707E7227DBA06C35E95262E7FFF8F8E60CBC7A69C`

## Verified

Measured on a real install, 2026-10-07 (Neverness to Everness, UE 5.6.1, 27 mods installed):

| what | result |
|---|---|
| game package index | **250,092 packages** built in **2.2 s** from the six `pakchunk*.utoc` |
| all 27 installed mods | every one `RESOLVES` (exit 0), assets ranging 3–40 per mod |
| **negative control** | built an index from the *shader chunk only* (7,558 packages); a real mod then reported **DRIFT, exit 1**, naming all five of its orphaned packages |
| unknown control | a missing path returns **exit 2**, never 0 |
| self-test | `pakdrift --self-test` — 4/4, includes a case that must go red |

**The negative control matters more than the passes.** It proves the tool can say no on real
inputs, which is the only thing that makes the 27 passes mean anything.

## The honest limits

- It is a **name-level** compare. It catches renamed/removed targets. It does not yet compare
  offsets, hashes, or asset internals — an offset-level mode is the upgrade path.
- `retoc`'s asset-registry parse **panics on NTE's containers** (a name-map assertion). This
  tool uses `retoc manifest`, which works; it does not use the registry parse.
- `global.utoc` is skipped by name: it is the script-object store and holds no game packages.

Read-only. It does not modify the game, the mods, or anything else.

MIT.
