# Silver Retrieval Set v1

`retrieval_questions.jsonl` is an evidence-first development set, not a
frozen product-quality gate.  Every answerable case points to an actual active
canonical Chunk from the isolated evaluation workspace and records:

- PDF SHA-256;
- Docling parser version and chunker version;
- current Chunk UID and Chunk text hash;
- page and section fallback anchors;
- language, task type, review status and construction metadata.

The runner validates those anchors before scoring retrieval.  A changed PDF,
parser, chunker, page range or Chunk hash fails validation rather than quietly
comparing a new index to stale labels.

The initial set contains 18 cases: 17 answerable cases across eight public
papers and one no-answer negative.  It includes English, mixed Chinese/English
queries, method/formula/table/long-context questions and a multi-Chunk BERT
case.  The latter uses two required evidence groups so a single partial Chunk
is not mistaken for complete answer evidence.

Run only against the isolated workspace:

```powershell
cd backend
& $PaperGraphPython run_rag_eval.py validate --cases tests/golden/silver/retrieval_questions.jsonl
& $PaperGraphPython run_rag_eval.py retrieval --cases tests/golden/silver/retrieval_questions.jsonl --limit 10
```

Embedding and rerank experiments also require `--run-external`; this prevents
credentials in a developer environment from causing billable calls during an
ordinary local test run.  Silver results are diagnostic and may be revised with
an auditable change record.  They must not be presented as a reviewed Golden
result.
