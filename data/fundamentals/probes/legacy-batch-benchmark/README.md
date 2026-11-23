# Original-page recognition timing

The benchmark used pages 1 and 2 of three previously archived exchange PDFs, selected from the existing legacy worklist at positions 1050, 1815 and 2777. The same six pages went through all three modes, with mode order rotated across the three PDFs. The host also ran regular research work. The timings exclude file setup, comparisons and financial-field interpretation.

| Method | Render six pages | Recognize six pages | Total |
| --- | ---: | ---: | ---: |
| Existing Poppler PNG and one Vision process per page | 4.19 s | 5.97 s | 10.16 s |
| Two pages per Poppler PNG and Vision invocation | 4.16 s | 5.00 s | 9.16 s |
| Two Poppler RGB pages per invocation, lossless PNG level 1 and batched Vision | 1.50 s | 4.39 s | 5.89 s |

All six matched on image dimensions, the SHA-256 of decoded RGB pixels, canonical Vision boxes (including exact coordinates) and filed-field decisions. These six pages had no positive legacy proof. Two additional reads used separate original pages with already-signed positive financial cells. Both reproduced the original word boxes, the entire numeric observation and decoded source pixels. The accepted cells were ₹136.15 crore in quarterly revenue and ₹6.79 in basic EPS. Together the two checks used 20 optical reads. The measured elapsed time fell 42% on the six benchmark pages. The full workflow also performs hashing, source extraction, caching and field attribution.

The new PNG has the same 8-bit RGB pixels. Its compressed bytes differ from the earlier Poppler PNG. Each positive proof stores the actual PNG, its hash, the source PDF member and page, the decoded RGB hash and the renderer version. The original cache and its signed PNGs remain available. To reproduce a new image, pass the original PDF bytes, the saved page number and a temporary directory to `fundamental_legacy_batch.render_pages(directory, pdf, page, page)` on the pinned Poppler/zlib versions. Hash its returned PNG to compare with the receipt. The saved Swift source digest and machine details identify the recognizer.

[timings.json](timings.json) records all six page comparisons. [confirmed-positive-cells.json](confirmed-positive-cells.json) names the signed original receipts. [run.py](run.py) and [positives.py](positives.py) reproduce the bounded comparisons. The benchmark software's full-file fingerprint predates a cleanup of unused cache variables. Its recognizer, RGB renderer and encoding operations are the same.
