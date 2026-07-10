The key facts are now clear. Let me confirm the access control on `verify_transaction_inclusion`.

### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Internal Merkle Node Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` but remains a live, unrestricted public NEAR contract method. Any external caller can invoke it directly with a `ProofArgs` where `tx_id` is an internal Merkle tree node hash, bypassing the coinbase proof guard that `verify_transaction_inclusion_v2` enforces, and obtain a `true` return value for a fabricated transaction.

---

### Finding Description

`verify_transaction_inclusion` is decorated with `#[deprecated]` and `#[pause]`, but carries no `#[private]`, no predecessor check, and no other access control. [1](#0-0) 

`#[deprecated]` is a Rust compile-time lint. It emits a compiler warning for Rust callers but has zero effect on NEAR RPC dispatch — the method remains fully callable by any external account.

The function's only verification is: [2](#0-1) 

`compute_root_from_merkle_proof` is a pure iterative hash computation that treats its first argument as an opaque 32-byte value with no distinction between leaf transaction hashes and internal node hashes: [3](#0-2) 

The code itself documents this gap explicitly: [4](#0-3) 

`verify_transaction_inclusion_v2` closes this gap by requiring a valid coinbase proof at index 0 before delegating to v1: [5](#0-4) 

The `From<ProofArgsV2> for ProofArgs` conversion intentionally drops `coinbase_tx_id` and `coinbase_merkle_proof`: [6](#0-5) 

Because v1 is still directly callable, an attacker can skip v2 entirely and submit `ProofArgs` with an internal node as `tx_id`.

---

### Impact Explanation

An attacker can call `verify_transaction_inclusion` with:
- `tx_id` = an internal Merkle node hash from a real block in the contract's headers pool
- `tx_index` and `merkle_proof` crafted so that `compute_root_from_merkle_proof` returns the block's real `merkle_root`

The function returns `true`, falsely asserting that a non-existent transaction is included in a confirmed Bitcoin block. Any downstream system (bridge, oracle, cross-chain protocol) that calls this contract method to gate asset releases or state transitions is deceived into accepting a fabricated transaction proof.

---

### Likelihood Explanation

The attack requires no privileges, no key compromise, and no special chain state. The attacker only needs:
1. A block hash present in the contract's `headers_pool` (publicly readable)
2. Knowledge of that block's Merkle tree (public Bitcoin data)
3. The ability to call a NEAR view/change method (open to all)

The 64-byte internal node technique is well-documented (the BitMEX post is cited in the contract itself). The exploit is mechanically straightforward.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with `#[private]` so it is only callable by the contract itself (as it is from `verify_transaction_inclusion_v2`). Deprecation annotations alone provide no runtime protection on NEAR.

---

### Proof of Concept

```
1. Read any block hash B from the contract's headers_pool.
2. Fetch block B's raw transaction list from a Bitcoin node.
3. Compute the Merkle tree. Pick any internal node N at depth d, position p.
4. Construct a merkle_proof of length d such that
   compute_root_from_merkle_proof(N, p, proof) == block.merkle_root.
5. Call verify_transaction_inclusion with:
     tx_id             = N          (internal node, not a real txid)
     tx_block_blockhash = B
     tx_index          = p
     merkle_proof      = proof
     confirmations     = 0
6. The contract returns true.
```

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-368)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
}
```

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
