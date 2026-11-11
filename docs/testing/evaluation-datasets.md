# Evaluation dataset controls (AER-030)

The catalog at `src/finagent/evaluation/dataset-catalog.json` registers dataset
versions, canonical JSON SHA-256 values, per-scenario family/customer/story
lineage, exact-input fingerprints, change reasons and contamination history.
It is a reviewed source artifact. It is not a digital signature or a substitute
for restricted access, source control or independent AML/Compliance review.

## Current collection

`aml-synthetic-v1` is development-only. It has already been used for development
and regression. Its original scenarios and version are preserved. Its catalog
registration records that exposure. It must not be relabelled as an independent
validation or release set. No validation or locked release dataset is supplied
by this change, and the complete AER-030 requirement remains unfinished.

## Enforced behavior

- All dataset JSON files must be registered; each scenario needs lineage.
- Family/customer/story overlap across splits is rejected, including historical
  or retired registrations. Case-only label changes do not evade this check.
- Exact input reuse across splits is rejected even when scenario/transaction IDs,
  expected labels or family labels are changed. This does not detect every
  semantic near duplicate. Lineage declarations still require independent review.
- Current contents must match their registered version and history. Development
  or validation changes require a new version and appended history.
- Locked release datasets cannot be rewritten under the same dataset name.
- Recorded contamination blocks use of that version. Record the incident in a
  new history entry and current registration; do not delete or clear history.
- Evaluation `purpose` is `development` by default. Explicit `validation` and
  `release` purposes require matching datasets. A release run additionally
  requires all three splits to be present and checked.
- Promotion, rollback and enforced promoted-configuration checks require a
  passing evaluation of the same configuration, with matching current locked
  release dataset/catalog evidence. Development evaluations can still report
  `PASS_PROPOSED`, but cannot authorize promotion.
- Previously promoted rows are retained for lineage. Historical development-only
  evidence no longer satisfies `require_promoted_configuration=True` checks.
- Dataset/catalog changes invalidate promotion evidence until regression is run
  again. No existing database rows or committed effects are deleted.

## Adding independent sets

Have dataset owners supply genuinely distinct families, customers and story
lineage. Do not produce holdouts by editing identifiers or small values in
existing development cases. Declare lineage for every scenario (including
control/retrieval scenarios), review near duplicates and source provenance,
register content and input digests, and append a reasoned history record.
Keep `history` append-only through code review and source control.

The controller exposes `record_for` to calculate a registration record from
reviewed content; calculating a hash does not establish independence or approval.
The test fixtures demonstrate controller behavior only and are not release data.

The API accepts:

```json
{"dataset": "an-independently-reviewed-release-dataset", "purpose": "release"}
```

No such dataset is installed by this patch. Thresholds remain PROPOSED; dataset
separation does not satisfy two-reviewer labeling (AER-031), approve thresholds
or constitute production acceptance. The UI currently sends the default
`development` purpose; release orchestration/UI work requires real release sets.

## Validation

Run Ruff, mypy, unit tests, then the backend suite. The existing governance
integration test first proves actual rejection of development promotion. Its
remaining promotion/rollback mechanics use an explicitly isolated release-check
dependency. Separate unit tests exercise real release-evidence validation against
temporary test-only catalogs; they do not certify a real release collection.
