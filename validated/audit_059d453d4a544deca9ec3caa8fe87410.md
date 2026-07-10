### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Nodes as Valid Transaction Proofs — (`contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains a publicly callable NEAR entry point and contains no guard against the 64-byte transaction Merkle proof forgery. An unprivileged caller can supply an internal Merkle tree node hash as `tx_id` with a crafted sibling proof, causing the function to return `true` for a transaction that does not exist in the block.

---

### Finding Description

`verify_transaction_inclusion` delegates proof verification entirely to `merkle_tools::compute_root_from_merkle_proof`. That helper hashes the caller-supplied `tx_id` with the proof siblings and compares the result to the stored Merkle root. There is no check that `tx_id` is a leaf node (an actual transaction) rather than an internal node of the Merkle tree. [1](#0-0) 

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

`compute_root_from_merkle_proof` simply iterates the proof, hashing left or right depending on position parity, with no depth or leaf-validity constraint: [2](#0-1) 

**Concrete exploit for a 4-transaction block (T1, T2, T3, T4):**

| Symbol | Value |
|--------|-------|
| H12 | SHA256d(T1 \|\| T2) — internal node |
| H34 | SHA256d(T3 \|\| T4) — internal node |
| Root | SHA256d(H12 \|\| H34) |

Attacker calls `verify_transaction_inclusion` with:
- `tx_id` = H12 (an internal node, **not** a real transaction)
- `tx_index` = 0
- `merkle_proof` = `[H34]`

`compute_root_from_merkle_proof(H12, 0, [H34])` = SHA256d(H12 \|\| H34) = Root ✓

The function returns `true`, falsely certifying H12 as an included transaction.

The `#[deprecated]` Rust attribute generates only a compiler warning; it does **not** prevent the function from being invoked on-chain. The function carries no `#[private]` guard and is reachable by any NEAR account. [3](#0-2) 

The code's own documentation acknowledges the flaw: [4](#0-3) 

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."

`verify_transaction_inclusion_v2` was introduced to mitigate this by requiring a coinbase proof of equal length, which pins the tree depth: [5](#0-4) 

However, the v1 function remains independently callable and unprotected.

---

### Impact Explanation

Any downstream NEAR contract or bridge that calls `verify_transaction_inclusion` to authorize cross-chain actions (releasing bridged assets, confirming payments, unlocking collateral) can be deceived into accepting a fraudulent proof. The attacker does not need to mine blocks, control a privileged role, or possess any special access — only a real block already submitted to the contract and public knowledge of its Merkle tree structure are required. The corrupted proof result is: the function returns `true` for a hash that is not a valid Bitcoin transaction.

---

### Likelihood Explanation

The attack requires only a submitted mainchain block (available immediately after initialization) and the ability to call a public NEAR function. Bitcoin Merkle tree structures are fully public. No permissions, mining capability, or key material are needed. Likelihood is high for any deployment where downstream contracts consume the v1 verification result.

---

### Recommendation

Apply one of the following:

1. **Remove the external entry point**: Add `#[private]` to `verify_transaction_inclusion` so only the contract itself can call it (used internally by `verify_transaction_inclusion_v2`).
2. **Hard-deprecate**: Replace the function body with `env::panic_str("use verify_transaction_inclusion_v2")` to force migration.
3. **Add depth consistency**: Require callers to also supply a coinbase proof of equal length directly in the v1 function, matching the mitigation already present in v2.

---

### Proof of Concept

1. Submit a real Bitcoin block with 4 transactions to the contract via `submit_blocks`.
2. Compute H12 = SHA256d(T1 ‖ T2) — the left internal Merkle node.
3. Compute H34 = SHA256d(T3 ‖ T4) — the right internal Merkle node.
4. Call `verify_transaction_inclusion` with:
   - `tx_id` = H12
   - `tx_block_blockhash` = the submitted block hash
   - `tx_index` = 0
   - `merkle_proof` = `[H34]`
   - `confirmations` = 1
5. The function returns `true`, falsely certifying H12 as an included transaction, despite H12 being an internal Merkle node with no corresponding Bitcoin transaction.

### Citations

**File:** contract/src/lib.rs (L276-281)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
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

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-369)
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
    }
```

**File:** merkle-tools/src/lib.rs (L34-52)
```rust
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
