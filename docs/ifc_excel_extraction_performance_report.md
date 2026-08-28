# IFC → Excel extraction performance report

## Production baseline and measurement status

The reported production baseline is a roughly **38 MB IFC**, **~6.5 GB peak RSS**, and **more than 7–8 minutes elapsed**, while using about **1.1 of 8 vCPUs**. That production file is not present in this repository, so it would be misleading to invent post-change RSS or elapsed figures. Every job now returns peak RSS and stage timings so the same input can provide an apples-to-apples result after deployment.

The result payload reports input-independent counts for extracted elements, properties, COBie rows, and protocol rows. Logs report IFC entity count, entities processed, rows written, current RSS, peak RSS, and elapsed seconds for each requested stage.

## Refactor

Extraction now uses `openpyxl.Workbook(write_only=True)`. Rows are appended as they are extracted instead of first being retained in Python lists and pandas DataFrames. In particular, the Properties worksheet retains only one property row and one occurrence's local values at a time. Header styling is retained, while body cells receive no per-cell styles.

Configuration is applied before expensive work: selected classes use `model.by_type(class_name)`, property/quantity-set filters run before property value conversion, and spatial, type, and classification work is skipped unless requested. Lightweight caches contain type handles, one expansion per referenced type, and STEP-id-to-spatial-name tuples. Occurrence property dictionaries are not cached across the model.

Classification associations are traversed once per element for all three Uniclass outputs. Type properties are expanded once per referenced type rather than once for every occurrence. The web process only writes a worker specification; it does not open the IFC. Parent and isolated worker RSS are logged separately.

## Structures removed

* Complete `prop_rows`, classification rows, COBie rows, and corresponding whole-model DataFrames.
* Duplicate `all_objects` / `all_export_objects` collections when sheets do not need them.
* Whole-model occurrence `psets_cache`.
* A second normal-mode workbook validation load and memory-heavy fallback export.
* Three classification relationship traversals per occurrence.

## Benchmark procedure

Run the production fixture through the isolated job and retain all `EXCEL_EXTRACTION_STAGE`, `EXCEL_EXTRACTION_PARENT_MEMORY`, and `EXCEL_EXTRACTION_COMPLETED` records. Compare sheet row counts against the baseline workbook, then report input bytes, IFC entity count, extracted elements, property rows, per-sheet row counts, old/new peak RSS, old/new elapsed, largest-RSS stage, and slowest stage. The desired acceptance target remains below 2 GB peak RSS with materially shorter elapsed time.

No strict RSS assertion is in normal unit tests because allocator and native IfcOpenShell behaviour varies by platform. The regression test instead checks the streaming result contract and exact worksheet/property row count.

## Further optimisation

First benchmark this single-process streaming implementation. If CPU remains the limiting factor, safe concurrency should be evaluated only for work based on primitive snapshots or independent input files; sharing IfcOpenShell handles or opening multiple copies of the same model is intentionally avoided.
