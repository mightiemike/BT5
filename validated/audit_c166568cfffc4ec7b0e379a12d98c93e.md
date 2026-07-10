### Title
Merkle Proof Malleability in `verify_transaction_inclusion` Allows SPV Proof Forgery - (`contract/src/lib.rs`)

### Summary
The deprecated `verify_transaction_inclusion` (v1) function remains callable by any unprivileged NEAR account. It does not validate that the supplied `tx_id` is a leaf node of the Merkle tree. An attacker can supply an internal Merkle tree node as `tx_id` with a truncated proof path, causing the function to return `true` for a transaction that was never included in the block. This is the direct analog of ECDSA signature malleability: just as the same private key can produce two distinct valid byte strings for the same message, the same Merkle root can be "proven" to contain either a real leaf transaction or a synthetic internal-node "transaction."

### Finding Description
`verify_transaction_inclusion` computes `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof)` and compares the result to `header.block_header.merkle_root`. [1](#0-0) 

Because the function accepts any 32-byte value as `tx_id` without checking whether it is a leaf-level hash, an attacker can supply an internal node of the Merkle tree as `tx_id`. The internal node, combined with its sibling subtree hash as the sole proof element, hashes up to the correct Merkle root, causing the function to return `true`.

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is a pure positional hash-chain computation with no leaf-vs-internal-node distinction: [2](#0-1) 

The function is marked `#[deprecated]` and carries an explicit `# Warning` comment acknowledging the vulnerability: [3](#0-2) 

However, Rust's `#[deprecated]` attribute only generates compiler warnings for Rust callers. External NEAR callers invoking the method via RPC are not blocked in any way. The function remains a live, publicly exposed contract method.

The v2 function was introduced to fix this by requiring a coinbase proof of equal depth: [4](#0-3) 

But v1 was never removed or access-gated, leaving the vulnerable path open in parallel.

### Impact Explanation
Any downstream NEAR contract that calls `verify_transaction_inclusion` (v1) to gate a privileged action — releasing bridged funds, confirming a cross-chain swap, crediting a deposit — can be deceived into accepting a forged proof. The attacker does not need to mine a block or hold any privileged role. They only need to know the Merkle tree structure of any confirmed Bitcoin block already tracked by the light client, which is public information.

### Likelihood Explanation
The function is publicly exposed on the deployed contract and callable by any NEAR account without any role check or access control. The Merkle tree structure of every Bitcoin block is publicly available on-chain. The attack requires no special resources beyond a NEAR account and knowledge of the target block's transaction set.

### Recommendation
Remove `verify_transaction_inclusion` (v1) from the contract entirely, or add an explicit access-control guard (e.g., `#[private]`) to prevent external calls. All callers must be migrated to `verify_transaction_inclusion_v2`, which requires a coinbase proof of equal depth to establish that the supplied `tx_id` is at leaf level.

### Proof of Concept
Consider a block with 4 transactions T0, T1, T2, T3:

```
H01  = SHA256d(T0 || T1)       ← internal node
H23  = SHA256d(T2 || T3)       ← internal node
Root = SHA256d(H01 || H23)     ← merkle_root stored in the block header
```

An attacker calls `verify_transaction_inclusion` with:
- `tx_id` = `H01` (an internal node, not a real transaction)
- `tx_index` = `0`
- `merkle_proof` = `[H23]`

Inside `compute_root_from_merkle_proof`:
- `current_position = 0` (even) → `current_hash = SHA256d(H01 || H23) = Root`

`Root == header.block_header.merkle_root` → function returns `true`, falsely asserting that `H01` is a confirmed transaction in the block.

A recipient contract checking `verify_transaction_inclusion(...) == true` before releasing funds would be fully deceived by this forged proof, with no on-chain evidence of fraud.

### Citations

**File:** contract/src/lib.rs (L276-286)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
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
