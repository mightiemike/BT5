# Q11043: nonce-sensitive batching reorder in transactions::old_timeout_format_parses_on_new_client

## Question
Can an unprivileged attacker submit multiple transactions for one signer through public RPC in quick succession that reaches `chain/jsonrpc-primitives/src/types/transactions.rs::old_timeout_format_parses_on_new_client` with control over transaction order and batching or parallel submission timing and make nearcore reorder nonce-sensitive work between admission and execution without preserving the signer’s intended sequence, breaking the invariant that public submission paths must preserve canonical nonce ordering for one signer, and leading to transaction manipulation?

## Target
- File/function: `chain/jsonrpc-primitives/src/types/transactions.rs::old_timeout_format_parses_on_new_client`
- Entrypoint: submit multiple transactions for one signer through public RPC in quick succession
- Attacker controls: transaction order and batching or parallel submission timing
- Exploit idea: reorder nonce-sensitive work between admission and execution without preserving the signer’s intended sequence
- Invariant to test: public submission paths must preserve canonical nonce ordering for one signer
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a parallel submission test for consecutive nonces and assert execution order matches canonical nonce order
