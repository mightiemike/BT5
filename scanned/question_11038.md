# Q11038: nonce-sensitive batching reorder in call_function::RpcCallFunctionResponse

## Question
Can an unprivileged attacker submit multiple transactions for one signer through public RPC in quick succession that reaches `chain/jsonrpc-primitives/src/types/call_function.rs::RpcCallFunctionResponse` with control over transaction order and batching or parallel submission timing and make nearcore reorder nonce-sensitive work between admission and execution without preserving the signer’s intended sequence, breaking the invariant that public submission paths must preserve canonical nonce ordering for one signer, and leading to transaction manipulation?

## Target
- File/function: `chain/jsonrpc-primitives/src/types/call_function.rs::RpcCallFunctionResponse`
- Entrypoint: submit multiple transactions for one signer through public RPC in quick succession
- Attacker controls: transaction order and batching or parallel submission timing
- Exploit idea: reorder nonce-sensitive work between admission and execution without preserving the signer’s intended sequence
- Invariant to test: public submission paths must preserve canonical nonce ordering for one signer
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a parallel submission test for consecutive nonces and assert execution order matches canonical nonce order
