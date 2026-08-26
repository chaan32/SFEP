# SFEP pre-Bundle contract repair implementation plan

> Status: approved continuation of the binding equipment-quality design. This addendum resolves contradictions discovered after Tasks 1–7 and must be completed before the original Task 8 CLI is sealed.

**Goal:** Preserve the finished Task 1–7 semantics while replacing partial golden examples and open lineage contracts with a complete, producer-independent oracle that Task 8 Python output and the Java Adapter can consume byte-for-byte.

**Non-negotiable constraints:**

- Keep the existing 12-material CP949 golden source unchanged.
- Never import `equipment_quality` from the independent golden semantic oracle.
- Never add an update/bless mode for golden expectations.
- Keep the seven normative root schemas byte-identical to their packaged wheel resources and pin their literal SHA-256 values.
- Do not use holdout data to create, tune, filter, or grade criteria.
- Keep existing Task 1–7 public wire output byte-compatible unless this plan explicitly repairs a normative contract.
- All generated real-data Bundles and runtime environments remain ignored and local.

## Corrected dependency order

1. Contract and canonical-number correction.
2. Complete independent golden semantic oracle and frozen expectations.
3. Range/rule provenance sidecars.
4. Task 8 core: criteria projection, summary, identities, atomic artifacts.
5. Task 9a runtime-verifier slice only.
6. Task 8 CLI orchestration.
7. Original Task 9 build/runtime/golden sealing.
8. Original Task 10 actual-data verification.
9. Java Adapter and Swing monitor.

Full Task 9 must not move before Task 8: its producer wheel and source digests would immediately become stale. Only runtime verification moves before the CLI because `run_analysis` must fail closed before reading CSV or publishing output.

## Repair 1: close the normative summary contract

**Files:**

- Modify `docs/superpowers/specs/2026-08-25-sfep-equipment-quality-early-warning-design.md`
- Modify `docs/superpowers/plans/2026-08-26-sfep-equipment-quality-python-plan.md`
- Modify `contracts/equipment-monitor/v1/analysis_summary.schema.json`
- Modify `analysis/equipment_quality/contracts/v1/analysis_summary.schema.json`
- Modify `analysis/equipment_quality/schema.py`
- Modify `analysis/tests/test_schema_resources.py`
- Modify `analysis/tests/test_contracts.py`
- Modify `contracts/equipment-monitor/v1/canonical-number-test-vectors.json`

**Acceptance:**

- `quarantineCounts` accepts only the exact Task 3 reason vocabulary.
- `labelCensoringCounts` accepts only the exact censor vocabulary.
- Required `chargePurgeCounts.outer` and `.inner` record purged Charge and row counts.
- `holdoutMetrics` is exactly two ordered profiles: `DANGER`, then `CAUTION_OR_DANGER`.
- Each of the six metrics owns `lower`, `upper`, `validReplicates`, and `reasonCode`; zero denominator and too-few-bootstrap states cannot be confused with valid intervals.
- `lineage.populations` contains exactly REFERENCE, DISCOVERY, CONFIRMATION, HOLDOUT in that order.
- Aggregate conditionals bind ranges to REFERENCE and rules to matching DISCOVERY/CONFIRMATION populations.
- Every lineage artifact role has a closed output-field grammar; `analysis_summary.lineage.*` remains the sole recursive-metadata exception.
- Dependency terminals are a closed union of the 50 source columns, config/runtime/schema/source/identity/population/policy roots, and valid output nodes.
- Raw mappings have exact source role/column, non-null stage, and no dependencies. Derived mappings have null source role/column and non-empty closed dependencies; derived replay fields may have a stage.
- Replay identifiers map to top-level `charge_id`, `slab_no`, `hr_coil_id`, and `ap_prod_id`, never `values_json.*`.
- `f_bfg_per`, `f_cog_per`, and `f_ldg_per` are present in raw lineage/dependency vocabulary.
- Conversion, filter, and transformation values use a closed vocabulary enforced by schema or application validation.
- Root, packaged resource, production digest pin, test literal, and installed-wheel bytes advance together; the other six schema digests remain unchanged.
- Add binary64 tie vectors including `0x1.1111111111111p-3 → 0.13333333333333332`; the independent test serializer implements the global shortest/UTF-8 lexicographic rule rather than relying on `repr()`.

**Verification:**

```bash
analysis/.venv/bin/python -W error -m pytest \
  analysis/tests/test_contracts.py \
  analysis/tests/test_schema_resources.py \
  analysis/tests/test_deterministic.py -q
```

## Repair 2: replace partial golden examples with a complete frozen oracle

**Files:**

- Create `analysis/tests/oracles/__init__.py`
- Create `analysis/tests/oracles/golden_semantics.py`
- Modify `analysis/tests/test_contracts.py`
- Replace all files under `contracts/equipment-monitor/v1/golden-expectation/` as required

**Oracle independence:**

- Standard library only.
- Parse the three CP949 sources directly.
- AST/test enforcement forbids imports from `equipment_quality` and forbids update/write modes.
- Implement join/quarantine precedence, T/D split, stage surfaces, type-1 bins, fixed candidates, fallback metrics, ordering, canonical JSON/CSV, and ID preimages independently from the binding design and checked-in config.
- Compare computed semantic bytes with immutable checked-in expectations.
- Production output is separately compared byte-for-byte with the same expectations.
- Task 9 seal may substitute only approved provenance tokens; it never computes or updates semantics.

**Literal fixture checkpoints:**

- Boundary/replay/quality rows: `12 / 12 / 11`.
- Quarantine source records: `3` (`DUPLICATE_AP_KEY=2`, `UNLINKED_AP=1`).
- `asOf=2025-02-20`, maturity cutoff `2025-01-13`, inner split `2025-01-03`.
- Reference `10`, discovery `5`, confirmation `2`, holdout `2`.
- Criteria projection: `268` lines = `261` range inputs + `7` mature-quality inputs; canonical SHA-256 `6842fabd1cc79ca801e34bb0708498f4bea16317d4449e6d773e28e40c94ff5c`.
- Operating ranges: `0`.
- Quality rules: `165` = `113` numeric + `25` categorical + `27` interaction; all `INSUFFICIENT_EVIDENCE`; ordered rule-ID-list SHA-256 `32873af746cdaf4e300f3efd136074cf98f6ed601f0d2d6c7c856d98144deee2`.
- Replay: `95` events = `7×12+11`; ordered event-ID-list SHA-256 `efbc5963a584dd79b84e2455044645d7d51b1f4c5d6721b0b2b38f9ccf2142ab`.
- Summary: 50 source profiles, 37 drift entries, two holdout profiles, 12 materials, four populations, zero range aggregates, 330 quality aggregates, and complete leaf lineage.
- Alerts: `expectedReplayEventCount=95`, `alerts=[]`. Positive alert behavior belongs to a separate synthetic Java rule-engine fixture because the fixed support thresholds make a positive golden rule impossible with 12 materials.

## Repair 3: retain exact statistical input provenance

**Files:**

- Modify `analysis/equipment_quality/models.py`
- Modify `analysis/equipment_quality/operating_ranges.py`
- Modify `analysis/equipment_quality/quality_intervals.py`
- Modify their focused tests/factories

**Interfaces:**

- Add immutable `OperatingRangesResult` and `QualityRulesResult` rich results.
- Add `build_operating_ranges_result(...)` and `build_quality_rules_result(...)`.
- Preserve existing `build_operating_ranges(...) -> list[dict]` and `build_quality_rules(...) -> list[dict]` as byte-compatible wrappers around the same private core.
- Range sidecars retain UTF-8-sorted finite contributor material keys per emitted rule.
- Quality sidecars retain, per rule and split, fixed adjustment fields/bands, informative stratum keys, discovery weights, candidate material keys, and comparator counts.
- Do not store comparator SHA tuples or send lineage through bootstrap workers; comparator records resolve from fixed population/stratum-minus-candidate expressions.
- Rich `to_wire()` returns fresh plain mappings and must equal legacy output byte-for-byte.
- Reject missing/duplicate material catalog identities, invalid SHA URIs, non-finite weights, duplicate strata, candidate/comparator overlap, and record/sidecar ID mismatch.

## Task 8 core: projection, summary, identity, atomic publication

Implement original Task 8 Steps 1–3 and the non-CLI portion of Step 5 against the repaired schema and frozen oracle.

- `SummaryBuildRequest` carries complete frozen criteria/bundle identities, source descriptors, schema descriptors, genealogy, split, ranges/rules plus their sidecars, events, and definitions.
- Identity maps accept exactly the specified 8/19 keys and schema-role sets.
- Criteria projection excludes holdout bidirectionally and includes mature reference inputs.
- Summary lineage is referentially closed, unique, sorted, acyclic, and traces every artifact/Manifest leaf except the lineage subtree itself.
- Atomic writer validates canonical in-memory payloads before exposure, writes six fixed artifacts, fsyncs files and directories, writes Manifest last, renames on the same filesystem, and handles same-ID reuse/nondeterminism/durability-unknown exactly as specified.
- Replay bytes already validated by Task 7 must not undergo a second full schema-validation pass.

## Task 9a: runtime verifier slice

Implement only:

- `analysis/equipment_quality/runtime_verify.py`
- `analysis/tests/test_runtime_identity.py`
- `analysis/tests/factories/runtime.py`

`verify_runtime()` returns a deeply immutable identity containing the exact verified manifest bytes, so Task 8 cannot re-read a changed file. It authenticates canonical/schema-valid bytes, platform/Python/interpreter/pip/environment policy, installed distribution versions and RECORD-backed code-tree bytes, and embedded producer provenance without needing a source tree, lock files, or wheel archive.

Before implementation, freeze these runtime contract decisions in the design/tests:

- Wheel archive SHA is a build/seal attestation and is not falsely re-derived at runtime.
- Embedded provenance has an exact canonical schema and binds one named third-party lock (`locks.requirements`); remaining lock attestations are verified by the seal tools.
- Map logical producer `equipment-quality` explicitly to distribution `sfep-equipment-quality`.
- Define exact allowed installed-distribution inventory and bootstrap-tool exclusions.

## Task 8 CLI completion

- Four mandatory absolute non-symlink paths.
- `verify_runtime` runs before CSV loading or output creation.
- Use the returned exact runtime bytes for IDs and the copied artifact.
- Fixed order: read/validate → genealogy → split → projection/criteria ID → rich ranges/rules → bundle ID → events/summary → atomic write.
- Development editable installs are valid for unit tests only through injected/mocked frozen runtime identity; they must not be presented as a production runtime.

## Task 10 corrections carried forward

- Raw source shapes are `(23649,15)`, `(23652,26)`, `(23641,9)`.
- In-memory `InputTables` shapes are `(23649,16)`, `(23652,27)`, `(23641,10)` because `_source_record_number` is required provenance.
- Actual paths come only from `SFEP_STEEL_DATA_DIR`; no hard-coded development path or guessed repository path.
- Every emitted range/rule support must equal its sidecar membership count.

## Review gates

Each repair/task is implemented by one owner with TDD, committed locally, packaged as a scoped diff, then reviewed by a fresh read-only reviewer. Critical/Important findings return to the original owner for at most five repair rounds. No Git push or merge is performed by the implementation workflow.
