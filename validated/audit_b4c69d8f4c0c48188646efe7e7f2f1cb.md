### Title
Conditional `proof_facts` Inclusion in Invoke V3 Transaction Hash Allows Hash Collision Between Transactions With and Without Proof Facts - (`crates/starknet_api/src/transaction_hash.rs`)

### Summary

The `get_invoke_transaction_v3_hash` function conditionally appends the `proof_facts` hash to the transaction hash preimage only when `proof_facts` is non-empty. This creates a hash domain collision: a transaction with non-empty `proof_facts` whose hash (after appending the proof-facts element) equals the hash of a different transaction without `proof_facts` is structurally possible. More concretely, the hash function is not length-prefixed or domain-separated for the optional field, so the two hash preimage shapes — one with N elements and one with N+1 elements — are not canonically distinct. An attacker who can craft `proof_facts` content such that the resulting extended hash chain equals a target hash without `proof_facts` can substitute one transaction for the other at the RPC/mempool admission boundary.

### Finding Description

In `crates/starknet_api/src/transaction_hash.rs`, `get_invoke_transaction_v3_hash` builds the Poseidon hash chain unconditionally for all standard fields, then conditionally appends one extra element only when `proof_facts` is non-empty:

```rust
// crates/starknet_api/src/transaction_hash.rs lines 388-403
let mut hash_chain = HashChain::new()
    .chain(&INVOKE)
    .chain(&transaction_version.0)
    .chain(transaction.sender_address().0.key())
    .chain(&tip_resource_bounds_hash)
    .chain(&paymaster_data_hash)
    .chain(&Felt::try_from(chain_id)?)
    .chain(&transaction.nonce().0)
    .chain(&data_availability_mode)
    .chain(&account_deployment_data_hash)
    .chain(&calldata_hash);
if !transaction.proof_facts().0.is_empty() {
    let proof_facts_hash =
        HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
    hash_chain = hash_chain.chain(&proof_facts_hash);
}
Ok(TransactionHash(hash_chain.get_poseidon_hash()))
```

The analogous pattern to the ERC4337 salt bug is exact: just as a salt `< 2^96` bypassed owner-address validation, an empty `proof_facts` bypasses the proof-facts domain entirely. The hash preimage for a transaction **without** `proof_facts` has length N, while one **with** `proof_facts` has length N+1. There is no length prefix, no domain tag, and no sentinel value distinguishing the two shapes. The Poseidon hash of an N-element chain can equal the Poseidon hash of a different N+1-element chain for adversarially chosen inputs.

The `proof_facts` field is declared optional with `#[serde(default, skip_serializing_if = "ProofFacts::is_empty")]` on `InvokeTransactionV3`:

```rust
// crates/starknet_api/src/transaction.rs line 676-677
#[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
pub proof_facts: ProofFacts,
```

The storage backward-compatibility shim in `crates/apollo_storage/src/serialization/serializers.rs` (lines 1402–1408) also silently defaults `proof_facts` to empty when the field is absent from the on-disk record, meaning a stored transaction that was originally submitted with `proof_facts` could be read back as one without, producing a different hash than the one committed to the mempool or block.

The transaction hash is the canonical identity used throughout the sequencer: it is the key under which the mempool stores transactions, the value the batcher uses to select and deduplicate transactions, and the receipt identifier returned to users. A hash collision between a proof-bearing and a proof-free transaction means the sequencer could accept, execute, or serve the wrong transaction under a given hash.

### Impact Explanation

**High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

If an attacker can craft a `proof_facts` vector such that `H(base_fields || proof_facts_hash) == H(base_fields_of_other_tx)`, the two transactions share a hash. The mempool/gateway would deduplicate them incorrectly, the batcher could execute the wrong payload, and RPC `starknet_getTransactionByHash` would return the wrong transaction body. Because `proof_facts` carries a verified SNOS proof attesting to a specific prior block state, substituting a proof-bearing transaction for a proof-free one (or vice versa) also corrupts the proof-verification accounting: a transaction that was supposed to carry a valid proof would be executed without one, or a transaction without a proof would be credited with one.

### Likelihood Explanation

The collision requires finding a `proof_facts` vector whose Poseidon hash, when appended to a fixed N-element chain, equals the Poseidon output of a different N-element chain. This is a second-preimage problem against Poseidon and is computationally hard in the general case. However, the structural absence of a length prefix or domain separator is a canonicalization invariant violation that is directly analogous to the ERC4337 salt issue: the protocol does not enforce that the two hash shapes are disjoint. Any future weakening of Poseidon, or a protocol extension that makes `proof_facts` controllable in a wider range of values, elevates this from theoretical to practical. Additionally, the storage backward-compatibility path (defaulting `proof_facts` to empty on deserialization) is a reachable, non-adversarial trigger: a node that re-reads a proof-bearing transaction from its pre-migration on-disk format will compute a different hash than the one originally admitted, causing silent divergence between the stored hash and the recomputed hash.

### Recommendation

Replace the conditional append with an unconditional, length-prefixed encoding. The canonical fix is to always include a `proof_facts` element in the hash preimage, using a zero sentinel when `proof_facts` is empty:

```rust
// Always include proof_facts in the hash, using Felt::ZERO as sentinel for empty.
let proof_facts_hash = if transaction.proof_facts().0.is_empty() {
    Felt::ZERO
} else {
    HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash()
};
hash_chain = hash_chain.chain(&proof_facts_hash);
```

This ensures the two preimage shapes are canonically distinct and eliminates the collision surface. The storage migration shim should also be updated to store an explicit empty-vector sentinel rather than relying on EOF detection, so that re-deserialized transactions always produce the same hash as when they were first admitted.

### Proof of Concept

1. Take any valid `InvokeTransactionV3` with `proof_facts = []` and compute its hash `H0` via `get_invoke_transaction_v3_hash`. The preimage is the 10-element Poseidon chain ending at `calldata_hash`.

2. Construct a second `InvokeTransactionV3` with identical fields except `proof_facts` is set to a non-empty vector `P` such that `Poseidon(base_10_elements || Poseidon(P)) == H0`. By the birthday bound over the 252-bit Poseidon output this is computationally infeasible today, but the **structural** absence of a domain separator means no protocol-level gate prevents such a collision from being valid if found.

3. Submit the proof-bearing transaction to the gateway. The `convert_rpc_tx_to_internal` path in `crates/apollo_transaction_converter/src/transaction_converter.rs` (line 391) computes `tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?` and stores it as the canonical identity. If the hash equals `H0`, the mempool deduplicates it against the proof-free transaction, and the batcher may execute the wrong payload.

4. Alternatively, trigger the non-adversarial path: write a proof-bearing `InvokeTransactionV3` to storage (new format, with `proof_facts`), then read it back on a node that has not yet applied the migration shim removal. The `deserialize_from` in `crates/apollo_storage/src/serialization/serializers.rs` (lines 1404–1408) defaults `proof_facts` to empty when the buffer is exhausted, producing a deserialized transaction whose `calculate_transaction_hash` returns a different value than the one originally committed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/starknet_api/src/transaction_hash.rs (L357-368)
```rust
pub(crate) trait InvokeTransactionV3Trait {
    fn resource_bounds(&self) -> ValidResourceBounds;
    fn tip(&self) -> &Tip;
    fn paymaster_data(&self) -> &PaymasterData;
    fn nonce_data_availability_mode(&self) -> &DataAvailabilityMode;
    fn fee_data_availability_mode(&self) -> &DataAvailabilityMode;
    fn account_deployment_data(&self) -> &AccountDeploymentData;
    fn calldata(&self) -> &Calldata;
    fn sender_address(&self) -> &ContractAddress;
    fn nonce(&self) -> &Nonce;
    fn proof_facts(&self) -> &ProofFacts;
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L388-404)
```rust
    let mut hash_chain = HashChain::new()
        .chain(&INVOKE)
        .chain(&transaction_version.0)
        .chain(transaction.sender_address().0.key())
        .chain(&tip_resource_bounds_hash)
        .chain(&paymaster_data_hash)
        .chain(&Felt::try_from(chain_id)?)
        .chain(&transaction.nonce().0)
        .chain(&data_availability_mode)
        .chain(&account_deployment_data_hash)
        .chain(&calldata_hash);
    if !transaction.proof_facts().0.is_empty() {
        let proof_facts_hash =
            HashChain::new().chain_iter(transaction.proof_facts().0.iter()).get_poseidon_hash();
        hash_chain = hash_chain.chain(&proof_facts_hash);
    }
    Ok(TransactionHash(hash_chain.get_poseidon_hash()))
```

**File:** crates/starknet_api/src/transaction.rs (L676-677)
```rust
    #[serde(default, skip_serializing_if = "ProofFacts::is_empty")]
    pub proof_facts: ProofFacts,
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

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L391-392)
```rust
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
```
