# Docling — offline candidate benchmark, not an automatic parser replacement

Docling (MIT license, upstream docling-project/docling) can reconstruct complex
PDF layouts and tables. The existing native PDF/DOCX/XLSX parsers remain
authoritative. Replacing them without preserving source page/cell citations,
embedded-image provenance and reliable structured codes could reduce accuracy.

This opt-in script compares the current native PDF parser against Docling
using privately human-verified expected text snippets. It reports aggregate
phrase recall ONLY; no original content, expected strings, product codes
or source path is written to output, PostgreSQL, Qdrant or a vendor.
The private labels must remain off GitHub. The script never indexes results.

## Offline operator setup

- Review and pin a Docling release, dependency tree and local model assets.
  Install the optional candidate only in a separate, network-restricted
  benchmark environment. Do not change the production lockfile.
- Pre-provision model artifacts at a private --local-models directory.
  Explicitly disable outbound traffic at OS/container level as well as
  setting both HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1.
- Prepare a manually checked private JSON list of expected text phrases.
  Never derive ground truth from either parser's previous output.
- No OCR or third-party plugins are enabled by this candidate. Tables are
  parsed in local-only mode. It is limited to PDFs of 25 MiB or smaller.

Default dry-run validates files and labels without loading Docling:

    python -m office_assistant.scripts.evaluate_docling --pdf /private/sample.pdf --truth /private/truth.json --local-models /private/docling-artifacts

Only inside a network-restricted operator environment, with Docling already
installed and artifacts staged, append --evaluate. Place an independent
CPU/time/memory sandbox around that command. Inspect aggregate phrase recall
and separately review PDF page numbers, table cell accuracy, product-code
exactness, false extractions and original document delivery. A higher recall
score alone does NOT permit production replacement of the current parser.

This is evaluation groundwork. Production integration must be proposed
as a separate opt-in adapter with source preservation, a rollback flag,
license/dependency review, synthetic and real corpus tests, and explicit
operator acceptance. Until then the Telegram bot is unchanged.


### Input/output safety (2026-10-21)

The evaluator rejects symlinked PDF/truth files, rejects non-PDF magic bytes before importing the optional Docling library, requires local model artifacts and explicit offline environment flags, and caps extracted Markdown at 8 Mi characters. This is an offline diagnostic only and never promotes Docling to the production ingestion pipeline. A representative owner-labeled document set and manual comparison remain required for acceptance.
