Looking at the full execution path and the exact code involved.

### Title
Coinbase Proof Depth Bypass via Internal Node as `coinbase_tx_id` Allows False Inclusion Proof — (`contract/src/lib.rs`, `merkle-tools/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` validates the coinbase proof by checking that `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root`. It never verifies that `coinbase_tx_id` is an actual leaf-level transaction hash. An attacker can supply an internal merkle node as `coinbase_tx_id` with a proof one level shorter than the real tree depth, satisfy the coinbase check, and then supply the same internal node as `tx_id` with the same short proof, causing the function to return `true` for a hash that is not a real transaction.

---

### Finding Description

The coinbase check in `verify_transaction_inclusion_v2` is:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [1](#0-0) 

`compute_root_from_merkle_proof` is a pure hash-chaining function with no concept of tree depth or leaf vs. internal node:

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
``` [2](#0-1) 

For a 4-transaction block with leaves `[tx0, tx1, tx2, tx3]`:

| Level | Nodes |
|-------|-------|
| Leaves | tx0, tx1, tx2, tx3 |
| Depth-1 | N01 = hash(tx0,tx1), N23 = hash(tx2,tx3) |
| Root | root = hash(N01, N23) |

The real coinbase proof is: `coinbase_tx_id=tx0`, `coinbase_merkle_proof=[tx1, N23]` (length 2).

The attack proof is: `coinbase_tx_id=N01`, `coinbase_merkle_proof=[N23]` (length 1).

`compute_root_from_merkle_proof(N01, 0, [N23])` = `hash(N01, N23)` = `root` ✓

The only other guard is the equal-length constraint:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
``` [3](#0-2) 

This forces `merkle_proof` to also have length 1. The attacker then sets `tx_id=N01`, `tx_index=0`, `merkle_proof=[N23]`:

`compute_root_from_merkle_proof(N01, 0, [N23])` = `root` ✓

Both checks pass. `verify_transaction_inclusion` (called via `args.into()`) returns `true` for `tx_id=N01`, an internal node. [4](#0-3) 

The underlying `verify_transaction_inclusion` explicitly documents this weakness but defers responsibility upward:

```
/// # Warning
/// This function may return `true` if the provided `tx_id` is a hash of an internal node
/// in the Merkle tree rather than a valid transaction hash.
/// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash
/// is performed at a higher level of verification.
``` [5](#0-4) 

`verify_transaction_inclusion_v2` was supposed to be that higher level, but it fails to enforce it.

---

### Impact Explanation

Any caller (bridge contract, payment verifier, cross-chain protocol) that calls `verify_transaction_inclusion_v2` and trusts its `true` return value as proof that a real Bitcoin transaction was included in a block can be deceived. The attacker proves inclusion of an internal node hash — a value that is not a transaction and was never broadcast — causing the downstream contract to act on a fabricated transaction identity. This is the exact 64-byte forgery class the v2 function was designed to close.

---

### Likelihood Explanation

The attack requires only public information: the transaction IDs of any block already stored in the contract's `headers_pool` (available from any Bitcoin block explorer). No privileged access, no key compromise, no social engineering. The attacker computes `N01 = double_sha256(tx0 || tx1)` offline and submits a single NEAR contract call. The entrypoint is fully public (`#[pause]` only, no `#[private]` or role gate). [6](#0-5) 

---

### Recommendation

The coinbase check must enforce that the proof depth equals the actual tree depth. The simplest fix is to require that `coinbase_merkle_proof.len()` equals the proof depth implied by the number of transactions in the block, or — more practically — to require that `coinbase_tx_id` is verified against a known-good coinbase txid supplied by a trusted source, or to add a minimum proof length check that prevents a proof shorter than `ceil(log2(tx_count))`. At minimum, the contract must reject any `coinbase_merkle_proof` whose length is less than `merkle_proof.len()` when the real tree depth is known, or reject proofs where `coinbase_tx_id == tx_id` at a non-zero index (which would also catch the degenerate self-proof case).

---

### Proof of Concept

```
Block with 4 txs: [tx0, tx1, tx2, tx3]
N01 = double_sha256(tx0 || tx1)
N23 = double_sha256(tx2 || tx3)
root = double_sha256(N01 || N23)   ← stored in block header

Attack call to verify_transaction_inclusion_v2:
  tx_id                = N01
  tx_block_blockhash   = <real block hash in headers_pool>
  tx_index             = 0
  merkle_proof         = [N23]          // length 1
  coinbase_tx_id       = N01            // internal node, not a real tx
  coinbase_merkle_proof= [N23]          // length 1, satisfies equal-length check
  confirmations        = 0

Step 1: length check: len([N23]) == len([N23]) → pass
Step 2: coinbase check: compute_root(N01, 0, [N23])
          = hash(N01, N23) = root → pass
Step 3: verify_transaction_inclusion(tx_id=N01, tx_index=0, merkle_proof=[N23])
          = compute_root(N01, 0, [N23]) = root == header.merkle_root → true

Result: verify_transaction_inclusion_v2 returns true for tx_id=N01,
        which is an internal node, not a real transaction.
```

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
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

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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
