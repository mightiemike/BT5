# Q14615: bounded rpc work underpricing in transactions::RpcFrom

## Question
Can an unprivileged attacker submit protocol-valid but expensive JSON-RPC transaction payloads that reaches `chain/jsonrpc/src/api/transactions.rs::RpcFrom` with control over payload structure and size that remain within normal public limits and make nearcore force disproportionate pre-execution processing before fees or metering apply, breaking the invariant that public transaction submission must bound preprocessing cost before expensive work is performed, and leading to high: non-network-level dos?

## Target
- File/function: `chain/jsonrpc/src/api/transactions.rs::RpcFrom`
- Entrypoint: submit protocol-valid but expensive JSON-RPC transaction payloads
- Attacker controls: payload structure and size that remain within normal public limits
- Exploit idea: force disproportionate pre-execution processing before fees or metering apply
- Invariant to test: public transaction submission must bound preprocessing cost before expensive work is performed
- Expected Immunefi impact: High: non-network-level DoS
- Fast validation: write a bounded expensive-RPC-input test and assert early validation rejects or cheaply short-circuits the slow path
