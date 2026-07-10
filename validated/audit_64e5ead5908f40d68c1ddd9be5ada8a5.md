### Title
DoS in `verify_transaction_inclusion` for Single-Transaction Blocks Due to Incorrect Empty Proof Rejection - (File: `contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` function (and by extension `verify_transaction_inclusion_v2`) unconditionally rejects empty merkle proofs via `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")`. However, an empty merkle proof is mathematically valid and correct for blocks containing exactly one transaction, where the merkle root equals the transaction hash directly. The underlying `compute_root_from_merkle_proof` function handles this case correctly, but the guard check panics before it is ever reached, causing permanent DoS for all valid single-transaction block proofs.

---

### Finding Description

In `contract/src/lib.rs` at line 315, `verify_transaction_inclusion` contains:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

In Bitcoin's Merkle tree construction, a block with exactly one transaction has a merkle root equal to that transaction's hash. The merkle proof for that transaction is empty — no sibling hashes are needed. The `compute_root_from_merkle_proof` function in `merkle-tools/src/lib.rs` correctly handles this: when called with an empty `merkle_proof` slice, the `for proof_hash in merkle_proof` loop does not execute, and the function returns `current_hash` (the transaction hash) unchanged. [2](#0-1) 

If `tx_id == header.block_header.merkle_root` (which is exactly true for a single-transaction block), the comparison at line 318–322 would return `true`. But the `require!` at line 315 panics unconditionally before that comparison is ever reached. [3](#0-2) 

The same root cause affects `verify_transaction_inclusion_v2` because it delegates to `verify_transaction_inclusion` internally: [4](#0-3) 

For `verify_transaction_inclusion_v2`, the coinbase merkle proof check at lines 358–365 would pass correctly (empty proof → returns `coinbase_tx_id` → equals `merkle_root`), but the subsequent call to `verify_transaction_inclusion` then panics on the empty-proof guard. [5](#0-4) 

---

### Impact Explanation

Any unprivileged NEAR caller — a downstream contract consuming SPV proofs, a relayer, or a direct user — that attempts to verify a transaction in a single-transaction block will always receive a panic/revert, even when supplying a mathematically correct and complete proof. The protocol's core verification functionality is permanently unavailable for this class of valid blocks. No funds are at risk, but the availability of the protocol's primary public API is broken for a well-defined set of valid inputs.

---

### Likelihood Explanation

Single-transaction blocks (containing only the coinbase transaction) exist throughout Bitcoin's history and can occur on any PoW chain supported by this contract (Bitcoin, Litecoin, Dogecoin, Zcash). The contract accepts such blocks via `submit_blocks` without issue. Any caller who subsequently attempts to verify the coinbase transaction of such a block will deterministically trigger the panic. The entry path requires no privileges: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are both public, unpermissioned functions gated only by the `#[pause]` attribute. [6](#0-5) [7](#0-6) 

---

### Recommendation

Remove the blanket rejection of empty proofs. Replace the current guard with a check that allows an empty proof when `tx_id` directly equals the block's `merkle_root`:

```rust
// Allow empty proof only when tx_id == merkle_root (single-transaction block)
if args.merkle_proof.is_empty() {
    return args.tx_id == header.block_header.merkle_root;
}
```

This mirrors the correct behavior already implemented in `compute_root_from_merkle_proof` and aligns with the Bitcoin protocol specification for single-transaction blocks.

---

### Proof of Concept

1. A Bitcoin block containing exactly one transaction (coinbase) is submitted via `submit_blocks` — this succeeds normally.
2. A caller invokes `verify_transaction_inclusion` with `tx_id = coinbase_hash`, `tx_index = 0`, `merkle_proof = []`, `tx_block_blockhash = <that block's hash>`, `confirmations = 1`.
3. The function panics at `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` — line 315.
4. Correct behavior: `compute_root_from_merkle_proof(coinbase_hash, 0, &[])` returns `coinbase_hash`; since `coinbase_hash == header.block_header.merkle_root` for a single-transaction block, the function should return `true`.
5. The same panic is triggered via `verify_transaction_inclusion_v2` because it calls `verify_transaction_inclusion` internally after its own coinbase proof check passes. [8](#0-7) [9](#0-8)

### Citations

**File:** contract/src/lib.rs (L287-288)
```rust
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

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
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
