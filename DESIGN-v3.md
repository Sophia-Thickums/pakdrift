# pakdrift v3 — design note (started 2026-10-07, before the lane's first working day)

## Finding 1 — WHY `retoc asset-registry` PANICS ON NTE (resolved, cheap)

**Deterministic, not encryption-related, and not NTE-specific.** It panics identically on a *mod*
container (which is unencrypted and fully readable):

    thread 'main' panicked at retoc/src/name_map.rs:54:5:
    assertion `left == right` failed
      left: 51539607911      (0x0C00000167)
      right: 3244556288      (0xC16C5B40)

Read: `retoc 0.1.5`'s **asset-registry** path does not understand this container version's name map.
NTE ships **UE 5.6.1** and the installed retoc is 0.1.5 — a version-support gap, not our bug and not
the game's encryption.

**Consequence, and it is good news:** the `manifest` path we actually use **works perfectly** — it returned
250,092 packages across the six game chunks and clean package names for every mod. So the tool does not
need the registry path, and the panic is a dead end worth NOT chasing.

**Decision for 1.1:** build the deeper compare on `retoc manifest` + `unpack`/`get` (chunk-level hashes),
never on `asset-registry`. Do not file an upstream bug for it yet — check whether 0.1.5 is simply behind a
newer UE version first, and only then decide.

## Finding 2 — the naming collision that shapes v3

`pakdrift` currently reads the **package name table** in the `.utoc`, not the contents. So it can say
*"this mod targets a package that no longer exists"* but cannot say *"this package exists and its
INTERNALS changed"* — which is the other half of patch drift (the mod compiles, loads, and renders
wrong because a material's structure moved under it).

**v3 = add the second question, in the same three-verdict frame:**
- name-level: the target package is gone → DRIFT (have this now)
- content-level: the target exists but its chunk hashes changed vs what the mod was built against
  → DRIFT at a different confidence (new)
- and the fail-shut rule carries: if a chunk cannot be read, the answer is UNKNOWN, never "unchanged".

The honest limit to state in the README: **content-level drift is only detectable against a snapshot of
the pre-patch game.** We need a before/after pair. So v3 should ship an `--snapshot` mode that records a
container's chunk hashes so a *future* patch can be diffed against it — otherwise the tool can only ever
answer the name-level question.
