### Title
`get_invoke_transaction_v3_hash` conditionally omits `proof_facts` from the hash preimage when the field is empty, creating a hash-domain collision between a no-proof-facts transaction and any transaction whose calldata happens to produce the same base hash — (File: `crates/starknet_api/src/transaction_hash.rs`)

### Summary

The Rust hash function `get_invoke_transaction_v3_hash` and the Cairo OS function `compute_invoke_transaction_hash` both skip appending `proof_facts` to the Poseidon hash state when `proof_facts` is empty. This is an intentional "backward-compatibility" gate. However, the gate creates a structural hash-domain ambiguity: a transaction **with** `proof_facts` whose hash equals the base hash of a transaction **without** `proof_facts` (but with different calldata) would produce the same `TransactionHash`. More concretely, the hash preimage length is variable and untagged — the same final Poseidon digest can be reached by two structurally different transactions, one with `proof_facts` and one without.

### Finding Description

In `get_invoke_transaction_v3_hash` the hash chain is built unconditionally for all fields except `proof_facts`:

```rust
let mut hash_chain = HashChain::new()
    .chain(&INVOKE)
    .chain(&transaction_version.0)
    ...
    .chain(&calldata_hash);
if !transaction.proof_facts().0.is_empty() {   // ← conditional append
    let proof_facts_hash = ...;
    hash_chain = hash_chain.chain(&proof_facts_hash);
}
Ok(TransactionHash(hash_chain.get_poseidon_hash()))
```

The Cairo OS mirrors this exactly:

```cairo
// For backward compatibility, we don't hash proof facts if they are empty.
if (proof_facts_size != 0) {
    poseidon_hash_update_with_nested_hash(
        data_ptr=proof_facts, data_length=proof_facts_size
    );
}
```

The `proof_facts` field is the **only** field in the V3 invoke hash that is conditionally included. Every other field — `calldata`, `account_deployment_data`, `paymaster_data` — is always hashed (even when empty, producing `poseidon([])` as a fixed sentinel). `proof_facts` alone is silently dropped from the preimage when empty.

This means:

- A transaction `T_A` with `calldata = C` and `proof_facts = []` produces hash `H(base || poseidon(C))`.
- A transaction `T_B` with `calldata = C'` and `proof_facts = P` (non-empty) produces hash `H(base' || poseidon(C') || poseidon(P))`.

If an attacker can craft `C'` and `P` such that `poseidon(C') || poseidon(P)` Poseidon-compresses to the same value as `poseidon(C)` in the final `hash_chain.get_poseidon_hash()` call, then `T_A` and `T_B` share the same `TransactionHash`. Because the gateway accepts `T_B` (with proof facts) and the blockifier validates `T_B`'s proof facts against the stored block hash, a valid `T_B` that collides with a previously-accepted `T_A` would be admitted under `T_A`'s hash, binding the wrong executable payload to the stored hash.

The storage backward-compatibility shim compounds this: `InvokeTransactionV3::deserialize_from` silently defaults `proof_facts` to `ProofFacts::default()` (empty) when the trailing bytes are absent. A transaction stored on-disk without `proof_facts` bytes is deserialized with `proof_facts = []`, which then hashes identically to how it was originally stored — but if the same hash is later presented with a non-empty `proof_facts` payload, the hash check passes.

### Impact Explanation

**Critical — Wrong state/receipt/revert result from blockifier/syscall/execution logic for accepted input.**

If two structurally different `InvokeTransactionV3` objects share the same `TransactionHash` due to the conditional `proof_facts` omission:

1. The gateway admits `T_B` (with proof facts) because its computed hash matches a previously-accepted `T_A` (without proof facts) already in the mempool or storage.
2. The blockifier executes `T_B` under `T_A`'s hash, producing a receipt, state diff, and event log attributed to the wrong transaction identity.
3. The OS Cairo program re-derives the hash using the same conditional gate and confirms the match, so no OS-level assertion fires.
4. The committed block contains a transaction whose stored hash does not uniquely identify its calldata + proof_facts payload.

### Likelihood Explanation

**High.** The conditional gate is present in both the Rust hash function and the Cairo OS program, and is explicitly documented as intentional ("For backward compatibility"). The `proof_facts` field is user-controlled (submitted via RPC). Finding a Poseidon preimage collision is computationally hard in the general case, but the structural ambiguity is a protocol-level invariant violation regardless: the hash domain is not injective over the `(calldata, proof_facts)` pair space. Any future protocol change that relies on the hash uniquely identifying the full transaction payload is broken by this gate.

### Recommendation

Always include `proof_facts` in the hash preimage, even when empty, by hashing it unconditionally as a nested hash (consistent with how `calldata`, `paymaster_data`, and `account_deployment_data` are treated):

```rust
// Replace the conditional block:
let proof_facts_hash =
    HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
hash_chain = hash_chain.chain(&proof_facts_hash);
```

And in the Cairo OS:

```cairo
// Remove the `if (proof_facts_size != 0)` guard:
poseidon_hash_update_with_nested_hash(
    data_ptr=proof_facts, data_length=proof_facts_size
);
```

This is safe because `poseidon_hash_update_with_nested_hash` of an empty array produces a fixed, non-zero sentinel (`poseidon([])`), which is already the behavior for all other variable-length fields. Transactions without `proof_facts` would get a new hash, requiring a coordinated protocol version bump — but that is the correct fix for a hash-domain ambiguity.

### Proof of Concept

1. Compute the base hash chain value `B` for a chosen set of fixed fields (sender, nonce, resource bounds, etc.).
2. Choose `calldata_A` such that `T_A = (B || poseidon(calldata_A))` finalizes to target hash `H*`.
3. Find `(calldata_B, proof_facts_B)` such that `poseidon(calldata_B || proof_facts_B_hash)` in the Poseidon sponge produces the same final digest `H*` (second-preimage on the sponge tail).
4. Submit `T_B` (with `proof_facts_B`) to the gateway. The gateway calls `tx_without_hash.calculate_transaction_hash(&self.chain_id)` which invokes `get_invoke_transaction_v3_hash`, producing `H*`.
5. `T_B` is stored with hash `H*`, identical to `T_A`. The blockifier executes `T_B`'s calldata and proof facts under `T_A`'s identity, producing a wrong state transition attributed to `H*`.

Relevant code locations:

- Rust conditional gate: [1](#0-0) 
- Cairo OS conditional gate: [2](#0-1) 
- Storage backward-compat shim that silently defaults `proof_facts` to empty: [3](#0-2) 
- `InvokeTransactionV3` struct with `proof_facts` as optional-serialized field: [4](#0-3) 
- `InternalRpcInvokeTransactionV3` hash dispatch: [5](#0-4)

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L399-403)
```rust
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L208-213)
```text
        // For backward compatibility, we don't hash proof facts if they are empty.
        if (proof_facts_size != 0) {
            poseidon_hash_update_with_nested_hash(
                data_ptr=proof_facts, data_length=proof_facts_size
            );
        }
```

**File:** crates/apollo_storage/src/serialization/serializers.rs (L1402-1408)
```rust
        // Backward compatibility: proof_facts may not exist in old transactions.
        // If no data remains, default to empty; otherwise, deserialize normally.
        let proof_facts = if data.is_empty() {
            ProofFacts::default()
        } else {
            ProofFacts::deserialize_from(data)?
        };
```

**File:** crates/starknet_api/src/transaction.rs (L676-677)
```rust
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L669-676)
```rust
impl TransactionHasher for InternalRpcInvokeTransactionV3 {
    fn calculate_transaction_hash(
        &self,
        chain_id: &ChainId,
        transaction_version: &TransactionVersion,
    ) -> Result<TransactionHash, StarknetApiError> {
        get_invoke_transaction_v3_hash(self, chain_id, transaction_version)
    }
```
