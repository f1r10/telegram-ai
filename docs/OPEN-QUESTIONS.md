# OPEN QUESTIONS & ASSUMPTIONS


| ID  | Topic        | Status | Description / Assumption |
| --- | ------------ | ------ | ------------------------ |
| Q1  | User Roles   | OPEN   | Confirm behavior difference between `admin` and `member`. Current assumption: Admin can upload org-level facts; member can only query and set personal memory/reminders. |
| Q2  | File Formats | OPEN   | RAG ingestion supports .txt, .md, text .pdf. Do we need .docx, .xlsx or image OCR in V1? |
| Q3  | AI Providers | OPEN   | Which specific LLM and Embedding models will be used in production (e.g., OpenAI, Qwen)? Must evaluate embedding model on Azerbaijani text. |
| Q4  | TG Transport | OPEN   | Long polling is default for V1. Should webhook implementation be prepared now? |
| Q5  | Facts Schema | OPEN   | Define exact JSON structure for company/customer facts. Assumption: Key-value with validity dates. |
| Q9  | Compliance   | OPEN   | Cross-border data transfer rules (Azerbaijan legislation). Technical assumption: Logs and DB will minimize PII. |
| Q13 | CI System    | OPEN   | GitHub Actions or GitLab CI? Assumption: Local Makefile serves as the CI runner baseline. |
| Q14 | Seed script  | OPEN   | `make seed` is defined per AGENTS.md but no seed dataset was specified. ASSUMPTION: target and content pending owner decision; the Makefile target fails with a pointer until defined. |
