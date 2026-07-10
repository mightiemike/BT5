### Title
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` Always Revert for Single-Transaction Blocks Due to Unconditional Empty-Proof Guard — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` unconditionally panics when `merkle_proof` is empty. For a Bitcoin block containing exactly one transaction, the valid Merkle proof **is** an empty vector (the transaction hash equals the Merkle root directly). The guard at line 315 permanently prevents SPV verification for this entire class of blocks. `verify_transaction_inclusion_v2` inherits the same defect because it delegates to the deprecated function.

---

### Finding Description

In `contract/src/lib.rs`, `verify_transaction_inclusion` contains:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

The guard at line 315 is unconditional. However, `compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` correctly handles an empty proof — it simply returns `transaction_hash` unchanged:

```rust
for proof_hash in merkle_proof {   // loop body never executes
    ...
}
current_hash   // returns transaction_hash as-is
``` [2](#0-1) 

For a block with exactly one transaction, the Bitcoin Merkle tree has a single leaf. The Merkle root **is** that transaction's hash, and the correct proof is an empty vector. The `require!` guard fires before the correct comparison can be made, causing an unconditional panic.

`verify_transaction_inclusion_v2` is also broken. It first validates the coinbase proof (which also uses `compute_root_from_merkle_proof` and correctly handles an empty `coinbase_merkle_proof`), then delegates to `verify_transaction_inclusion` via `args.into()`:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The `From<ProofArgsV2> for ProofArgs` conversion passes `merkle_proof` through unchanged: [4](#0-3) 

So for a single-tx block, both functions always panic at line 315 regardless of proof correctness.

---

### Impact Explanation

The `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` methods are the contract's core SPV API — the primary output consumed by bridges, atomic swaps, and cross-chain lending protocols built on top of this light client. Any downstream consumer that needs to verify a transaction in a coinbase-only block will receive a permanent revert. The transaction inclusion result is corrupted (forced to panic instead of returning `true`) for a valid, on-chain Bitcoin block class. This is a protocol-level broken invariant, not a gas or performance issue.

---

### Likelihood Explanation

Coinbase-only blocks occur on all supported chains (Bitcoin, Litecoin, Dogecoin, Zcash) — they are rare but real. Any relayer that submits such a block header to the contract will store it successfully. Any subsequent caller attempting SPV verification against that block will always get a panic. The entry path requires no privilege: `verify_transaction_inclusion_v2` is a public, unpaused method callable by any NEAR account.

---

### Recommendation

Remove the unconditional empty-proof guard and instead handle the single-transaction case explicitly:

```rust
if args.merkle_proof.is_empty() {
    return args.tx_id == header.block_header.merkle_root;
}
```

This mirrors the correct mathematical behavior: when the proof is empty, the transaction hash must equal the Merkle root directly.

---

### Proof of Concept

1. A Bitcoin block is mined containing only a coinbase transaction with hash `T`.
2. The block's `merkle_root` field equals `T` (single-leaf Merkle tree).
3. A relayer submits the block header via `submit_blocks` — this succeeds and stores the header.
4. A consumer calls `verify_transaction_inclusion_v2` with:
   - `tx_id = T`, `tx_index = 0`, `merkle_proof = []`
   - `coinbase_tx_id = T`, `coinbase_merkle_proof = []`
5. Step 1 check: `0 == 0` → passes. [5](#0-4) 
6. Coinbase check: `compute_root_from_merkle_proof(T, 0, &[])` returns `T`; `T == merkle_root` → passes. [6](#0-5) 
7. Delegates to `verify_transaction_inclusion(args.into())`.
8. `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` → **panics**. [7](#0-6) 

The call reverts. The valid proof is permanently unverifiable through this contract.

### Citations

**File:** contract/src/lib.rs (L315-322)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );
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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** merkle-tools/src/lib.rs (L42-51)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
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
