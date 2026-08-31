# Rules the rewrite must keep

Taken from the add-on shell being replaced. These are RULES, not UI: each one
encodes a decision that a fresh panel would quietly drop. The four tools can be
checked against this list.

## The licence gate

Access to EVE data is gated on accepting the EVE creator terms, and the gate is
enforced in **four independent layers**:

1. every gated operator's `poll`
2. the panel, which draws the licence box and returns before anything else
3. inside the workers themselves -- catalog load, preview, entry lookup,
   result population, auto-preview
4. at the network boundary: acceptance is passed down into the catalog and every
   resource fetch, which re-check it

Losing any layer weakens the gate. It is deliberately not a single check.

Acceptance is stored as an exact ACCEPTANCE ID, not a boolean, so publishing a
new terms revision silently invalidates every stored acceptance. Accept and
revoke both roll their two preferences back if saving preferences throws, so a
failed save never leaves a phantom acceptance. Revoking wipes derived state:
catalog, results, build, preview.

**Cache statistics and clear-cache are deliberately NOT gated** -- someone who
has revoked must still be able to delete what they downloaded.

## The cache

A shared, content-addressed store under Blender's DATAFILES at
`carbonenginejs/tool-core`, always normalised through
`abspath -> expanduser -> resolve`. Two sub-layouts are relied on: `ResFiles/`
for cached payload detection and `Previews/<build>/` for preview materialisation.

Paths are joined with `safe_join`, never plain concatenation -- that is path
traversal containment, not tidiness.

Clearing the cache removes downloaded payloads and previews ONLY. Indexes and
exported files are retained.

## Download policy

- The channel is Tranquility.
- Remote latest-build checks are rate-limited per cache, and the remaining wait
  is shown rather than silently swallowed.
- The default action is offline-first: with an exact build cached it never
  touches the network. Only an explicit refresh does.

## Jobs and threads

- **One job at a time.** A second launch is refused rather than queued.
- A worker may write ONE plain string, its progress line. Touching a
  PropertyGroup or tagging a redraw off the main thread crashes Blender rather
  than raising, so only the main-thread timer does either.
- Worker exceptions are caught as `BaseException` and surfaced in the UI. A
  failure must never be lost.

## The resource index

- The result list is a **capped projection**: absence from the list never means
  absence from the index, and the summary must say when it truncated.
- `_lowdetail` and `_mediumdetail` variants are hidden by default and the hidden
  count is reported.
- Selection survives repopulation by logical path, and selection callbacks are
  suppressed while repopulating -- otherwise repopulation triggers downloads.
- Auto-preview remembers the last FAILED path. Without that memo a failing
  preview retries forever; an explicit Retry is the only way back.

## Assembly

- Bundle-provided resources are overlaid by freshly downloaded ones.
- New objects are detected by a name-set diff around the import.
- Every imported object is stamped with `carbon_sof_dna` and
  `carbon_sof_geometry`.
- Problems are counted and reported, never fatal: a hull with one bad area still
  assembles.
- A DNA is normalised before anything else uses it, then resolved by the hosted
  service and stored through the validated resource cache.

## Dependencies

The GR2 and CMF importers ship in the same add-on as the EVE loader. Importing
geometry, assembling an existing bundle, and building DNA therefore share one
installed reader boundary; DNA resolution additionally requires the hosted
service.

## Interaction

Double-click is emulated from Blender's own `mouse_double_click_time` with a
floor and a single-entry memo. It is the only route to open-folder and
import-GR2 from the list.
