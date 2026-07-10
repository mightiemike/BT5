### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Transaction Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function is still publicly accessible to any unprivileged NEAR caller. It accepts any caller-supplied `tx_id` without validating that it corresponds to a real leaf-level transaction hash rather than an internal Merkle tree node. This is the exact class of missing input validation that `verify_transaction_inclusion_v2` was introduced to fix via coinbase proof anchoring. Because v1 remains callable, an attacker can supply an internal Merkle node hash as `tx_id`, construct a valid sibling proof for it, and receive a `true` verification result for a transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` (v1) is marked `#[deprecated]` but carries no access-control restriction beyond `#[pause]`. It is callable by any NEAR account in normal operation: [1](#0-0) 

The function's only validation on `tx_id` is that `merkle_proof` is non-empty. It then delegates directly to `compute_root_from_merkle_proof`, which treats the caller-supplied `tx_id` as a leaf hash and walks up the tree using the provided siblings: [2](#0-1) 

`compute_root_from_merkle_proof` performs no structural check — it simply hashes the input with each sibling in sequence: [3](#0-2) 

Because Bitcoin's Merkle tree is built by hashing pairs of 32-byte child hashes (64-byte inputs), every internal node hash is structurally indistinguishable from a leaf hash at the verification layer. An attacker can pick any internal node `N` at height `h` in a real block's Merkle tree, supply `tx_id = N`, `tx_index = position_of_N_at_height_h`, and `merkle_proof = [siblings above N]`. The computed root will equal the block's real Merkle root, so the function returns `true`.

`verify_transaction_inclusion_v2` was introduced specifically to close this gap by requiring a coinbase proof anchored at position 0: [4](#0-3) 

However, v2 does not remove or restrict v1. The contract's own warning acknowledges the flaw but does not prevent exploitation: [5](#0-4) 

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` (v1) to gate a financial action — for example, releasing bridged funds upon proof of a Bitcoin deposit — can be deceived. The attacker receives a `true` result for a transaction that was never broadcast or confirmed on Bitcoin. The corrupted invariant is the contract's core guarantee: `verify_transaction_inclusion` returning `true` must mean the supplied `tx_id` is a real, confirmed Bitcoin transaction in the specified block. That invariant is broken for any caller using v1.

---

### Likelihood Explanation

The attack requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly. The inputs needed — an internal Merkle node hash and its sibling path to the root — are derivable from publicly available Bitcoin block data. The 64-byte Merkle forgery technique is documented and well-understood. Likelihood is **medium**: the attack is realistic for any protocol that has not migrated all consumers to v2.

---

### Recommendation

Restrict `verify_transaction_inclusion` (v1) so it is no longer callable by external accounts. The simplest fix is to make it `pub(crate)` or remove the `pub` visibility entirely, since `verify_transaction_inclusion_v2` already calls it internally. Alternatively, add an explicit `#[private]` attribute (NEAR SDK) to prevent external invocation while preserving the internal call from v2.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature, `skip_pow_verification = false`) and submit a real Bitcoin block with a known Merkle tree, e.g., block 685452 used in the test suite.
2. From the block's Merkle tree, select any internal node `N` at height 1: `N = H(leaf_0 || leaf_1)`.
3. Compute the sibling path from `N` to the root (one level shorter than a full leaf proof).
4. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the submitted block hash
   - `tx_index` = the position of `N` in the height-1 layer
   - `merkle_proof` = the siblings above `N`
   - `confirmations = 1`
5. `compute_root_from_merkle_proof(N, position, siblings)` computes the correct Merkle root.
6. The function returns `true` — confirming a transaction that does not exist on Bitcoin. [6](#0-5) [3](#0-2)

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
