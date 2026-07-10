### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Tree Nodes as Valid Transaction IDs, Enabling Proof-Verification Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` function remains publicly callable on-chain despite being deprecated. It does not validate that the caller-supplied `tx_id` is an actual leaf-level transaction hash rather than an internal Merkle tree node hash. An unprivileged NEAR caller can supply a crafted `tx_id` corresponding to an internal node, causing the function to return `true` for a Bitcoin transaction that does not exist.

---

### Finding Description

**Vulnerability class:** Proof-verification forgery via insufficient input validation — the function validates only that the computed Merkle root matches the stored block root, but never validates that the supplied `tx_id` is a leaf node (real transaction) rather than an internal node.

**Analog mapping:** In the Symmetry report, `buy_state_rebalance` only checked balance changes for two specific `TokenAccounts` but did not validate which accounts were actually passed to the CPI, allowing substitution of different accounts. Here, `verify_transaction_inclusion` only checks that `compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof) == merkle_root`, but does not validate that `tx_id` is a real transaction (leaf node), allowing substitution of an internal Merkle tree node hash.

The function accepts five caller-controlled fields: [1](#0-0) 

All five are passed directly into the Merkle proof verifier with no leaf-node constraint: [2](#0-1) 

`compute_root_from_merkle_proof` is a pure hash-chain computation that treats any 32-byte input identically, whether it is a real transaction hash or an internal node: [3](#0-2) 

The only guards present are:
- `confirmations <= gc_threshold`
- `tx_block_blockhash` is in the mainchain
- enough confirmed blocks
- `merkle_proof` is not empty

None of these prevent an internal-node hash from being accepted as `tx_id`. The function's own documentation acknowledges this: [4](#0-3) 

The function is marked `#[deprecated]` but Rust's `#[deprecated]` is a compiler warning only — the method remains a live, callable NEAR contract entry point: [5](#0-4) 

The v2 function (`verify_transaction_inclusion_v2`) mitigates this by requiring a coinbase Merkle proof at index 0, which constrains the tree structure: [6](#0-5) 

But v1 is never gated behind v2 and remains independently reachable.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate an action (e.g., releasing bridged funds, minting wrapped tokens, settling a cross-chain swap) based on Bitcoin transaction inclusion can be deceived. An attacker can forge proof of a Bitcoin transaction that was never broadcast or confirmed, causing the downstream contract to execute as if a real Bitcoin payment occurred. This is a direct proof-verification forgery with concrete financial impact on any integrator relying on v1.

---

### Likelihood Explanation

Medium-High. The function is publicly callable by any NEAR account with no role restriction beyond the `#[pause]` flag (which is off by default). The 64-byte transaction Merkle forgery technique is well-documented and has known tooling. Any integrator that has not migrated to v2 — or any contract that calls v1 directly — is fully exposed. The deprecation notice does not remove the attack surface.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) entirely from the contract, or gate it so it always delegates to v2 internally. If removal is not immediately possible, add an explicit `require!(false, "use verify_transaction_inclusion_v2")` body to make it unconditionally revert. The coinbase-anchoring approach in v2 is the correct mitigation and should be the only exposed entry point.

---

### Proof of Concept

1. Attacker selects a confirmed Bitcoin block `B` with at least two transactions, already tracked by the light client.
2. Attacker computes the internal Merkle tree node `N` at depth 1, position 0 (i.e., `hash(tx0, tx1)`). `N` is 32 bytes and indistinguishable from a transaction hash to the contract.
3. Attacker constructs a `merkle_proof` of length `tree_depth - 1` that, starting from `N` at `tx_index = 0`, computes back to `B`'s stored `merkle_root`.
4. Attacker calls `verify_transaction_inclusion` with:
   - `tx_id` = `N` (internal node, not a real transaction)
   - `tx_block_blockhash` = hash of block `B`
   - `tx_index` = 0
   - `merkle_proof` = crafted proof (non-empty, length ≥ 1)
   - `confirmations` = 1
5. `compute_root_from_merkle_proof(N, 0, &proof)` returns `B.merkle_root`. The function returns `true`.
6. Any downstream contract consuming this `true` result treats the forged transaction as confirmed on Bitcoin.

### Citations

**File:** btc-types/src/contract_args.rs (L17-24)
```rust
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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
