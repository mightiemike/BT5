# Q784: parse atom parser non-canonical long-form zero via parse then execute

## Question
Can an unprivileged attacker reach `parse_atom` in `src/serde/parse_atom.rs` through public parsing or stream-analysis through `parse_atom` before execution, hashing, or serialization, using a crafted non-canonical long-form zero input and the parse then execute validation path while controlling deep cons-box structures and single-byte atom boundaries, so the code confusing atom length, cursor position, or pair construction, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that stream tree hash must equal deserialize-then-hash and causing Critical canonical serialization failure: ambiguous bytes become accepted?

## Target
- File/function: src/serde/parse_atom.rs::parse_atom
- Entrypoint: public parsing or stream-analysis through `parse_atom` before execution, hashing, or serialization
- Attacker controls: deep cons-box structures and single-byte atom boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for non-canonical long-form zero, drive it through parse then execute, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: stream tree hash must equal deserialize-then-hash
- Expected Immunefi impact: Critical canonical serialization failure: ambiguous bytes become accepted
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.
