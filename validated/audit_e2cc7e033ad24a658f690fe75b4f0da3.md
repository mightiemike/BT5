### Title
`account_deployment_data` field accepted by gateway/mempool but asserted zero in OS transaction hash computation — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

### Summary
The `account_deployment_data` field is declared in `InvokeTransactionV3`, included in the Rust-side `get_invoke_transaction_v3_hash` preimage, and accepted by the gateway without any emptiness check. However, the Cairo OS `compute_invoke_transaction_hash` function hard-asserts `account_deployment_data_size = 0` with an explicit TODO noting the field is not yet supported. A user who submits an invoke-V3 transaction carrying non-empty `account_deployment_data` will have it accepted by the gateway, hashed and stored by the sequencer, and included in a block — but the OS will trap on the assertion when the prover attempts to reproduce the hash, making the block unprovable.

### Finding Description
In the Rust hash path, `get_invoke_transaction_v3_hash` unconditionally chains `account_deployment_data` into the Poseidon preimage:

```rust
let account_deployment_data_hash = HashChain::new()
    .chain_iter(transaction.account_deployment_data().0.iter())
    .get_poseidon_hash();
// ...
.chain(&account_deployment_data_hash)
```

`InvokeTransactionV3Trait` requires every implementor to expose `account_deployment_data()`, and both `InvokeTransactionV3` and `InternalRpcInvokeTransactionV3` carry the field with no size restriction.

In the Cairo OS, the same function begins with:

```cairo
// TODO(Noa, 01/01/2026): remove the following `assert` once the field is supported.
assert account_deployment_data_size = 0;
```

This assert fires before the hash loop runs. Any non-zero `account_deployment_data_size` causes an immediate Cairo assertion failure, aborting the entire OS execution for that block.

The gateway conversion path (`convert_rpc_tx_to_internal`) copies `account_deployment_data` verbatim from the RPC transaction into `InternalRpcTransactionWithoutTxHash::Invoke` without checking whether it is empty. No stateful or stateless gateway validator was found that rejects a non-empty value.

### Impact Explanation
**High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

An unprivileged user submits an invoke-V3 transaction with `account_deployment_data = [0x1]`. The gateway computes a valid Poseidon hash (including the non-empty field), stores the `InternalRpcTransaction`, and the batcher includes it in a block. When the prover runs the OS over that block, `compute_invoke_transaction_hash` hits `assert account_deployment_data_size = 0` and aborts, rendering the entire block unprovable. The sequencer has committed a block it cannot prove.

### Likelihood Explanation
The field is part of the public Starknet V3 invoke transaction schema and is accepted by the JSON-RPC endpoint. Any client that sets `account_deployment_data` to a non-empty array produces a well-formed, signature-valid transaction that passes all current gateway checks. No special privilege or insider knowledge is required.

### Recommendation
Add an explicit gateway validation step (stateless or stateful) that rejects any invoke-V3 transaction whose `account_deployment_data` is non-empty until the Cairo OS assertion is removed and the field is fully supported end-to-end. The check should mirror the existing `proof_facts` emptiness guard pattern already present in the converter.

### Proof of Concept
1. Construct a valid `RpcInvokeTransactionV3` with `account_deployment_data: vec![Felt::ONE]` and a correct signature over the resulting hash (which includes the non-empty field per `get_invoke_transaction_v3_hash`).
2. Submit via the HTTP gateway endpoint. The gateway accepts it; `convert_rpc_tx_to_internal` stores it with `tx_hash` computed over the non-empty `account_deployment_data`.
3. The batcher pulls the transaction and the blockifier executes it successfully (the blockifier does not assert `account_deployment_data` is empty).
4. The block is committed. The prover runs the Starknet OS over the block; `compute_invoke_transaction_hash` reaches `assert account_deployment_data_size = 0` and aborts — the block is unprovable.