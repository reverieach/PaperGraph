# Golden Candidate v1 — Pending User Review

This is a **candidate**, not a Golden result and not a tuning set.  It is held
out from the initial Silver development set and is built from the five papers
added during the corpus expansion:

- SELF-RAG;
- Corrective Retrieval Augmented Generation (CRAG);
- BGE M3-Embedding;
- MMLongBench-Doc;
- the ACL 2024 Chinese RAG survey.

Every answerable case has an exact canonical Chunk UID and text hash plus
page/section/term fallbacks.  The candidate additionally records the source
PDF SHA-256, parser version and chunker version, so a stale corpus cannot
quietly yield a misleading Golden score.

## Required review

Before it can be run, review each case for:

1. question wording and answerability from only the designated paper;
2. evidence location, page anchor and required answer elements;
3. bilingual/Chinese phrasing and whether parser-produced CJK spacing makes a
   fallback term unreasonable;
4. absence of overlap with any Silver-driven parameter or prompt tuning.

The only permitted command before approval is provenance validation:

```powershell
cd backend
& $PaperGraphPython run_rag_eval.py validate --cases tests/golden/candidates/golden_v1/retrieval_questions.jsonl
```

Do **not** run `retrieval`, `--with-dense`, or `--with-rerank` against this
file until the user has reviewed it.  After approval, create a reviewed,
immutable `frozen_gold` copy rather than changing this candidate in place.
