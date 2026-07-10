### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable On-Chain, Enabling 64-Byte Merkle Proof Forgery - (`contract/src/lib.rs`)

---

### Summary

The contract exposes a deprecated SPV proof verification method, `verify_transaction_inclusion`, that is still a live, publicly callable NEAR contract method. It lacks the coinbase Merkle proof validation that was added in its replacement, `verify_transaction_inclusion_v2`, to close the 64-byte transaction Merkle proof forgery vulnerability. Any unprivileged NEAR caller can invoke the deprecated method directly, bypassing the security fix entirely.

---

### Finding Description

`verify_transaction_inclusion` is annotated with Rust's `#[deprecated]` attribute. In Rust, this attribute emits a **compiler warning only** — it does not restrict runtime access. The function remains `pub` and is compiled into the NEAR contract's exported ABI, making it callable by any NEAR account.

The function verifies SPV inclusion by computing a Merkle root from the caller-supplied `tx_id` and `merkle_proof` and comparing it to the stored block header's `merkle_root`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

There is no check that `tx_id` is a leaf-level transaction hash rather than an internal Merkle tree node. The contract's own documentation acknowledges this:

> *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."*

The replacement function `verify_transaction_inclusion_v2` closes this gap by requiring a separate coinbase Merkle proof that independently anchors the tree structure, preventing an attacker from substituting an internal node as a fake `tx_id`. However, because the old function is still exported, callers — including downstream bridge contracts, atomic swap protocols, or cross-chain lending protocols — can call it directly, receiving a forged `true` result.

---

### Impact Explanation

An attacker who knows the Merkle tree structure of any confirmed Bitcoin block can craft a `ProofArgs` where `tx_id` is an internal Merkle tree node (64 bytes when concatenated with its sibling), supply a valid-looking `merkle_proof` path, and cause `verify_transaction_inclusion` to return `true` for a transaction that does not exist. Any consumer contract that relies on this return value to authorize a cross-chain action (e.g., releasing funds, minting tokens, settling a swap) will be deceived into treating a non-existent Bitcoin transaction as confirmed.

---

### Likelihood Explanation

The entry path requires no privileges: any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte Merkle forgery technique is publicly documented (BitMEX research blog) and the required block Merkle tree data is publicly available from any Bitcoin full node. The contract itself documents the vulnerability in the deprecated function's docstring, confirming the attack vector is known and realistic.

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` or gate it behind an access-control role so it is no longer part of the exported NEAR contract ABI. Alternatively, delete the function body and have it unconditionally panic with a migration message. All callers must be directed to `verify_transaction_inclusion_v2`. The `#[deprecated]` Rust attribute alone provides no on-chain enforcement.

---

### Proof of Concept

1. Identify any confirmed Bitcoin block whose Merkle tree has at least two transactions. Obtain the Merkle tree's internal node hashes.
2. Select an internal node `N` at depth `d` from the root. Construct a `merkle_proof` of length `d` that walks from `N` up to the Merkle root stored in the on-chain header.
3. Call `verify_transaction_inclusion` with:
   - `tx_id` = `N` (an internal node, not a real transaction hash)
   - `tx_block_blockhash` = the hash of the confirmed block
   - `tx_index` = the position consistent with the proof path
   - `merkle_proof` = the constructed path
   - `confirmations` = any value ≤ `gc_threshold`
4. The function computes `compute_root_from_merkle_proof(N, tx_index, merkle_proof)`, which equals the stored `merkle_root`, and returns `true`.
5. Any consumer contract that called `verify_transaction_inclusion` to authorize a cross-chain action now acts on a forged proof.

**Root cause:** [1](#0-0)  — `#[deprecated]` is a compile-time hint only; the function is still `pub` and exported.

**Missing validation (present only in v2):** [2](#0-1)  — coinbase proof check that anchors the Merkle tree and prevents internal-node substitution.

**Vulnerable computation:** [3](#0-2)  — raw Merkle root recomputation with no leaf-vs-internal-node distinction.

**Acknowledged in docstring:** [4](#0-3)  — the contract itself warns that `tx_id` may be an internal node.

**`ProofArgs` accepts arbitrary `tx_id`:** [5](#0-4)  — no structural constraint on the `tx_id` field.

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

**File:** contract/src/lib.rs (L358-365)
```rust
        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );
```

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
