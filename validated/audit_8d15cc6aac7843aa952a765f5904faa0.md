Looking at the external bug's core pattern — **unconditional operation on a value without checking for a special variant that requires different handling** — I need to find the nearcore analog in fee/storage calculations, key-scheme handling, or protocol-boundary logic.

Let me trace the ML-DSA-65 key integration, specifically the `trie_id_len()` vs `len()` divergence documented as a known caveat.