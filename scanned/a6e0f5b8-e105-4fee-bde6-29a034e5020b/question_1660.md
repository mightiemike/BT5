# Q1660: tree hash for byte parser non-canonical long-form zero via pre-eval callback enabled versus disabled

## Question
Can an unprivileged attacker reach `tree_hash_for_byte` in `src/serde/de_tree.rs` through public parsing or stream-analysis through `tree_hash_for_byte` before execution, hashing, or serialization, using a crafted non-canonical long-form zero input and the pre-eval callback enabled versus disabled validation path while controlling deep cons-box structures and single-byte atom boundaries, so the code confusing atom length, cursor position, or pair construction, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that canonical bytes must map to one exact tree and causing Critical consensus divergence: one supported path accepts bytes another rejects?

## Target
- File/function: src/serde/de_tree.rs::tree_hash_for_byte
- Entrypoint: public parsing or stream-analysis through `tree_hash_for_byte` before execution, hashing, or serialization
- Attacker controls: deep cons-box structures and single-byte atom boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for non-canonical long-form zero, drive it through pre-eval callback enabled versus disabled, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: canonical bytes must map to one exact tree
- Expected Immunefi impact: Critical consensus divergence: one supported path accepts bytes another rejects
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.
