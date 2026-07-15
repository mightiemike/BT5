# Q15907: put resource limit enforcement

## Question

What can an unprivileged user do by writing contract storage, creating/deleting accounts, and generating state/proof boundary cases through valid transactions so that `put` in `core/store/src/trie/trie_storage.rs` (impl TrieCacheInner) processes large transactions, WASM bytecode, method args, access-key lists, receipts, RPC parameters, and storage writes along the trie, flat storage, state sync, and proofs path? User controls large transactions, WASM bytecode, method args, access-key lists, receipts, RPC parameters, and storage writes -> `put` processes that value during RPC admission, transaction validation, VM preparation/execution, trie access, and block/chunk resource accounting -> the user-controlled work is bounded by protocol gas, byte-size, recursion, proof, and queue limits before it can degrade consensus processing invariant might break -> potential in-scope impact is accepted non-network DoS, fee bypass, or consensus processing failure under the NEAR HackenProof scope. Exploit hypothesis: a valid user payload can drive this code into superlinear work or unbounded memory without paying the corresponding protocol cost, violating the actual protocol invariant that user-controlled work is bounded by protocol gas, byte-size, recursion, proof, and queue limits before it can degrade consensus processing.

## Target

- File/function: core/store/src/trie/trie_storage.rs:146::put
- Entrypoint: contract storage and account actions committed through Runtime::apply into core/store trie and flat-state paths
- User-controlled input: large transactions, WASM bytecode, method args, access-key lists, receipts, RPC parameters, and storage writes
- Attack path: User controls large transactions, WASM bytecode, method args, access-key lists, receipts, RPC parameters, and storage writes -> public entrypoint reaches `put` -> RPC admission, transaction validation, VM preparation/execution, trie access, and block/chunk resource accounting handles the value -> invariant failure could produce accepted non-network DoS, fee bypass, or consensus processing failure
- Security invariant: user-controlled work is bounded by protocol gas, byte-size, recursion, proof, and queue limits before it can degrade consensus processing
- Expected bounty impact: accepted non-network DoS, fee bypass, or consensus processing failure
- Fast validation approach: fuzz maximum-size payloads and worst-case storage/receipt patterns on a private testnet while asserting deterministic rejection or bounded gas-charged execution
