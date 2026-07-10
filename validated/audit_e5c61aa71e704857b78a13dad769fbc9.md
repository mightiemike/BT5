### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable and Accepts Internal Merkle Node Hashes as Valid Transaction IDs — (File: `contract/src/lib.rs`)

### Summary

The deprecated `verify_transaction_inclusion` (v1) function is still a live, unpermissioned public entry point on the NEAR contract. It contains a known proof-verification forgery flaw: it will return `true` when the caller supplies an internal Merkle tree node hash as `tx_id`, because the function performs no check that the supplied hash corresponds to a leaf (real transaction) rather than an intermediate node. Any recipient contract that gates cross-chain asset release on this function's return value can be deceived into accepting a forged inclusion proof.

### Finding Description

`verify_transaction_inclusion` is annotated `#[deprecated]` but is **not** access-controlled, not removed, and remains callable by any unprivileged NEAR account: [1](#0-0) 

The function's own doc-comment acknowledges the flaw explicitly: [2](#0-1) 

The core verification logic in `compute_root_from_merkle_proof` is purely positional — it hashes the supplied `tx_id` up the tree using the provided siblings and compares the result to the stored `merkle_root`: [3](#0-2) 

There is no check that `tx_id` is a leaf node. An attacker who knows the Merkle tree of any confirmed block can pick any internal node `N` at depth `d`, supply the `d` siblings that reconstruct the root from `N`, and the function returns `true`.

The v2 replacement (`verify_transaction_inclusion_v2`) mitigates this by requiring a coinbase proof of the same depth, but v1 is still reachable: [4](#0-3) 

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion` (v1) to authorize a cross-chain action — e.g., minting wrapped BTC, releasing bridged funds, or crediting a user account — will accept a forged proof and execute the action for a transaction that was never broadcast or confirmed. The corrupted value is the **proof result** (`true` for a non-existent transaction), which propagates directly into the authorization decision of the consuming contract.

### Likelihood Explanation

The attack requires only:
1. Knowledge of any confirmed Bitcoin block's Merkle tree (publicly available from any Bitcoin node or block explorer).
2. A single NEAR function call to `verify_transaction_inclusion` with a crafted `ProofArgs`.

No privileged role, private key, or social engineering is needed. The entry path is fully unpermissioned. The function is not paused by default. Likelihood is **high** for any deployment where a consuming contract has not independently migrated to v2.

### Recommendation

Remove `verify_transaction_inclusion` (v1) entirely from the contract, or gate it behind a role (e.g., `Role::DAO`) so it cannot be called by unprivileged accounts. Do not rely on the `#[deprecated]` annotation as a security boundary — it is a compiler hint only and does not restrict on-chain access. All callers must be migrated to `verify_transaction_inclusion_v2`.

### Proof of Concept

Given a confirmed mainchain block `B` with `merkle_root = R` and an internal node `N` at depth 1 (i.e., `N = SHA256d(tx0 || tx1)`):

1. Attacker calls `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real txid)
   - `tx_block_blockhash = B`
   - `tx_index = 0` (position of the left subtree)
   - `merkle_proof = [sibling_of_N, ...]` (the siblings from depth 1 up to the root)
   - `confirmations = 1`

2. `compute_root_from_merkle_proof(N, 0, proof)` reconstructs `R` correctly, because `N` is a genuine node in the tree.

3. The comparison `computed_root == header.block_header.merkle_root` is `true`.

4. The function returns `true` — a forged proof for a transaction that does not exist. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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
