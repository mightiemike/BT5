### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable On-Chain, Enabling 64-Byte Merkle Proof Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly invocable NEAR contract method despite being explicitly deprecated and self-documented as vulnerable to the 64-byte Merkle proof forgery attack. Rust's `#[deprecated]` attribute is a compile-time lint only — it imposes no on-chain access restriction. Any unprivileged NEAR caller can invoke v1 directly, bypassing the coinbase-proof mitigation introduced in `verify_transaction_inclusion_v2`, and obtain a `true` return value for a transaction that does not exist.

---

### Finding Description

The contract exposes two public proof-verification methods:

- `verify_transaction_inclusion` (v1) — marked `#[deprecated]` since 0.5.0, but still decorated `pub` and `#[pause]` (callable unless the contract is paused).
- `verify_transaction_inclusion_v2` — the intended replacement, which adds a coinbase Merkle proof check to pin the expected tree depth before delegating to v1. [1](#0-0) 

The v1 function accepts a caller-supplied `tx_id` and a `merkle_proof`, then computes the Merkle root and compares it to the stored block header's `merkle_root`: [2](#0-1) 

There is no check that `tx_id` is a leaf-level transaction hash. Bitcoin's Merkle tree does not distinguish leaf nodes from internal nodes — both are 32-byte hashes. An attacker who knows an internal node `H` at depth `d` in the tree can supply `tx_id = H` with a proof of length `tree_depth − d` (shorter than the full leaf-to-root path). `compute_root_from_merkle_proof` will traverse from `H` upward and arrive at the correct root, causing the function to return `true`. [3](#0-2) 

`verify_transaction_inclusion_v2` closes this gap by requiring a valid coinbase proof of the same length, which pins the expected tree depth: [4](#0-3) 

However, because v1 remains a public method, any caller can bypass v2 entirely and call v1 directly via NEAR RPC. The `#[deprecated]` attribute is invisible at the protocol layer.

---

### Impact Explanation

Any downstream contract, bridge, or dApp that calls `verify_transaction_inclusion` to gate an action (e.g., releasing locked funds, minting wrapped BTC, crediting a cross-chain transfer) receives a forged `true` result for a transaction that was never broadcast or confirmed on Bitcoin. The corrupted proof result is the direct output of a public contract method, consumed by callers who have no independent way to detect the forgery.

---

### Likelihood Explanation

The 64-byte Merkle forgery technique is publicly documented (referenced in the contract's own comments at line 267–268). The attacker needs only: (1) a mainchain block hash stored in the contract, (2) knowledge of any internal Merkle node in that block's transaction tree (obtainable from a Bitcoin full node), and (3) the ability to call a NEAR contract — no privileged role, no staked deposit beyond NEAR gas, no private key compromise. The deprecated v1 entry point is permanently open unless the contract is paused or upgraded. [5](#0-4) 

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or add a runtime guard that unconditionally panics:

```rust
pub fn verify_transaction_inclusion(&self, ...) -> bool {
    env::panic_str("verify_transaction_inclusion is removed; use verify_transaction_inclusion_v2");
}
```

Alternatively, gate the function behind an access-control role so it cannot be called by unprivileged accounts. Marking a method `#[deprecated]` in Rust does not restrict on-chain invocation.

---

### Proof of Concept

1. Identify any confirmed mainchain block hash `B` stored in the contract (via `get_block_hash_by_height`).
2. Fetch block `B` from a Bitcoin full node and extract its transaction Merkle tree. Identify an internal node `H` at depth `d` (e.g., the root of the left subtree at depth 1).
3. Construct a `merkle_proof` of length `tree_depth − d` that walks from `H` to the Merkle root using the sibling hashes at each level above `d`.
4. Call `verify_transaction_inclusion` directly via NEAR RPC with:
   - `tx_id = H` (the internal node, not a real transaction)
   - `tx_block_blockhash = B`
   - `tx_index` = any index consistent with `H`'s position
   - `merkle_proof` = the shortened path constructed above
   - `confirmations = 1`
5. The contract computes `compute_root_from_merkle_proof(H, tx_index, &proof)`, arrives at the correct Merkle root, and returns `true` — falsely asserting that the non-existent transaction `H` is included in block `B`. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L263-268)
```rust
    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
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
