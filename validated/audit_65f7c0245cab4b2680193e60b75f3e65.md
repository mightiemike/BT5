### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable and Vulnerable to Merkle Proof Forgery - (File: `contract/src/lib.rs`)

### Summary

The contract exposes a deprecated public function `verify_transaction_inclusion` that is vulnerable to the 64-byte transaction Merkle proof forgery attack. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase-proof mitigation added in `verify_transaction_inclusion_v2`, and receive a `true` result for a forged transaction inclusion proof.

### Finding Description

`verify_transaction_inclusion` is explicitly marked `#[deprecated(since = "0.5.0", note = "Use verify_transaction_inclusion_v2 instead.")]` because it lacks coinbase Merkle proof validation needed to defeat the 64-byte transaction forgery vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). [1](#0-0) 

Despite the deprecation, the function remains a live, unguarded public entry point. In Rust, `#[deprecated]` is a compile-time lint only; it imposes no runtime restriction. The function carries only `#[pause]`, meaning any unprivileged NEAR account can call it whenever the contract is not paused. [2](#0-1) 

The function's verification logic is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [3](#0-2) 

`compute_root_from_merkle_proof` simply hashes pairs of nodes up the tree without any check that the leaf (`tx_id`) is a real transaction rather than an internal Merkle node. [4](#0-3) 

The contract's own warning acknowledges this: *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."* [5](#0-4) 

`verify_transaction_inclusion_v2` closes the gap by first requiring a valid coinbase proof at index 0 before delegating to the deprecated function. [6](#0-5) 

### Impact Explanation

A recipient NEAR contract that calls `verify_transaction_inclusion` to gate a privileged action (e.g., releasing bridged funds, minting tokens, confirming a cross-chain payment) will receive `true` for a forged proof. The corrupted proof result is the exact state value that downstream authorization logic trusts. This enables an attacker to claim that an arbitrary Bitcoin transaction was confirmed in a real block without that transaction ever existing, leading to unauthorized fund releases or false cross-chain confirmations.

### Likelihood Explanation

The attack is well-documented (BitMEX research, 2018), requires no privileged role, no leaked keys, and no social engineering. The attacker only needs a real Bitcoin block's Merkle tree structure to derive a valid internal-node proof. The function is callable by any NEAR account whenever the contract is unpaused. Likelihood is **medium-high**: the technique is public knowledge and the entry point is open.

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with an access-control role so it cannot be called by unprivileged accounts. All callers should be migrated to `verify_transaction_inclusion_v2`. If backward compatibility must be preserved, the deprecated function should internally delegate to the v2 path (requiring a coinbase proof argument) rather than performing the unsafe bare Merkle check.

### Proof of Concept

1. Select any real Bitcoin block whose Merkle tree has depth ≥ 2. Let the Merkle root be `R` and let `N` be an internal node at depth 1 (i.e., `N = SHA256d(left_child || right_child)`).
2. Construct `merkle_proof = [sibling_of_N_at_depth_1]` and set `tx_index` so that `compute_root_from_merkle_proof(N, tx_index, [sibling])` yields `R`.
3. Call `verify_transaction_inclusion` on the NEAR contract with:
   - `tx_id = N` (the internal node, not a real transaction hash)
   - `tx_block_blockhash` = the real block's hash (already stored in the light client)
   - `tx_index`, `merkle_proof` as constructed above
   - `confirmations = 1`
4. The function returns `true`, falsely asserting that `N` is a confirmed transaction in that block.
5. Any downstream contract gating on this result will authorize the attacker's action. [7](#0-6) [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L277-280)
```rust
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

**File:** contract/src/lib.rs (L358-368)
```rust
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
