### Title
`verify_transaction_inclusion` Rejects Valid Empty Merkle Proofs for Single-Transaction Blocks — (`contract/src/lib.rs`)

---

### Summary

`BtcLightClient::verify_transaction_inclusion` contains a guard that unconditionally rejects any call where `merkle_proof` is empty. However, a Bitcoin block containing exactly one transaction (the coinbase) has a Merkle tree of depth zero: the Merkle root **is** the transaction hash, and the correct inclusion proof is an empty proof vector. The check therefore blocks a class of cryptographically valid proofs, mirroring the M-09 pattern of an overly-restrictive length guard that rejects legitimate inputs.

---

### Finding Description

In `contract/src/lib.rs` at line 315, the deprecated `verify_transaction_inclusion` function contains:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This check fires before the Merkle root computation at lines 318–322:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` is implemented as a loop over the proof elements:

```rust
for proof_hash in merkle_proof {
    ...
}
current_hash  // returned unchanged if proof is empty
``` [3](#0-2) 

When `merkle_proof` is empty the function correctly returns `transaction_hash` unchanged. For a block whose Merkle tree has exactly one leaf (the coinbase transaction), `merkle_root == tx_id`, so an empty proof is the **only** mathematically correct proof. The `require!` guard rejects it before the computation can confirm this.

The non-deprecated `verify_transaction_inclusion_v2` is equally affected because it delegates to the deprecated function at its final step:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [4](#0-3) 

The v2 path also enforces equal-length proofs for the coinbase and target transaction:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
``` [5](#0-4) 

For a single-transaction block both proofs are empty (`len == 0`), so this check passes. The coinbase proof check at lines 358–365 also passes because `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id`, which equals `merkle_root`. Execution then reaches `verify_transaction_inclusion`, which panics on the empty-proof guard. [6](#0-5) 

---

### Impact Explanation

Any NEAR caller invoking `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` with a valid proof for a transaction in a single-transaction Bitcoin block will always receive a panic (`"Merkle proof is empty"`), even though the proof is cryptographically correct. Downstream contracts or off-chain systems that rely on the contract's verification result to gate actions (e.g., cross-chain bridges, payment proofs) will be permanently unable to process such blocks. The corrupted invariant is the contract's guarantee that it correctly verifies all valid SPV proofs: it silently rejects a well-defined subset of them.

---

### Likelihood Explanation

Single-transaction blocks (containing only the coinbase) occur on Bitcoin mainnet during periods of low mempool activity and were common in early Bitcoin history. A relayer that has submitted such a block to the contract creates a state where no caller can ever prove inclusion of the coinbase transaction in that block, regardless of how correct their proof data is. The entry path requires no privilege: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, pausable-only functions callable by any NEAR account. [7](#0-6) [8](#0-7) 

---

### Recommendation

Allow `merkle_proof` to be empty and let the Merkle computation itself determine validity. Replace the blanket rejection:

```rust
// Before
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

with a check that only rejects proofs that are inconsistent with the claimed `tx_index` (i.e., a non-zero index with an empty proof is impossible):

```rust
// After
if !args.merkle_proof.is_empty() || args.tx_index != 0 {
    require!(!args.merkle_proof.is_empty(), "Merkle proof is empty for non-root transaction");
}
```

Or more simply, remove the guard entirely and rely on the Merkle root comparison at the end of the function to reject invalid proofs, since `compute_root_from_merkle_proof` already handles the empty-proof case correctly.

---

### Proof of Concept

1. A Bitcoin block containing only its coinbase transaction is submitted to the contract via `submit_blocks`. The block's `merkle_root` equals the coinbase `tx_id`.
2. A NEAR caller invokes `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_tx_id = merkle_root`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = merkle_root`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
3. The equal-length check passes (`0 == 0`).
4. The coinbase proof check passes: `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id == merkle_root`.
5. `verify_transaction_inclusion` is called; it hits `require!(!args.merkle_proof.is_empty(), ...)` and panics with `"Merkle proof is empty"`.
6. The call reverts. The proof — which is mathematically correct — is permanently rejected. [9](#0-8) [1](#0-0)

### Citations

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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
