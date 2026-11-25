# Maintenance tools

The following tools moved to the standalone `shani-sysadmin` app and are no
longer maintained here:

- `rerank.py` — `apps/shani-sysadmin/shani_sysadmin/ml_harness/rerank.py`

Their data (`reranker_corpus.json`, `build_reranker_corpus.py`) stays here
because Chronoa's model work still reads it. Import shims for the ML modules
that moved with them live in `shani_chronoa/` (see the app README).
