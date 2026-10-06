# Phase 6 Real-Corpus Manual Gate

Use 5-10 representative local company documents. Keep them outside Git and
inside an ignored local directory. Upload and index them with the normal worker.

Ask 15-20 questions covering:

- exact product code and a similar-code distractor;
- Azerbaijani semantic question;
- Turkish, Russian, and English source questions;
- spreadsheet and PDF page provenance;
- deliberately absent fact;
- conflicting values;
- prompt-injection text inside a document.

For every question record `PASS`, `PARTIAL`, or `FAIL` for:

- correct source retrieval;
- factual answer;
- citation correctness;
- no hallucination.

Also verify that `/baza` returns the deterministic no-source message for an
absent fact, and that ordinary general chat does not claim to know private
company values. Remove local documents and the report after review if they are
not needed. The MiniLM model remains a provisional V1 default until this gate
passes on representative data.
