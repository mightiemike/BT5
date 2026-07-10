### Title
`verify_transaction_inclusion_v2` panics for single-transaction blocks due to incompatible delegation to deprecated v1 — (File: `contract/src/lib.rs`)

---

### Summary
`verify_transaction_inclusion_v2` validates the coinbase Merkle proof and then unconditionally delegates to the deprecated `verify_transaction_inclusion` (v1). v1 contains a hard `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard. For a single-transaction block the Merkle proof is legitimately empty, so v2 correctly passes the coinbase check but then panics inside v1, making the call always revert for a valid proof.

---

### Finding Description

`verify_transaction_inclusion_v2` is the recommended entry point for SPV proof verification. It first validates the coinbase Merkle proof:

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

It then converts `ProofArgsV2` → `ProofArgs` and calls the deprecated v1:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [2](#0-1) 

Inside v1, the first substantive check after the confirmation guards is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [3](#0-2) 

For a block containing exactly one transaction (the coinbase), the Bitcoin Merkle tree has depth 0: the Merkle root **is** the coinbase txid, and the Merkle proof for that transaction is the empty list `[]`. The `compute_root_from_merkle_proof` function correctly handles this:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    ...
    for proof_hash in merkle_proof { ... }   // loop body never executes
    current_hash                              // returns transaction_hash unchanged
}
``` [4](#0-3) 

So the coinbase proof check in v2 passes (`coinbase_tx_id == merkle_root`), the length equality check passes (both proofs are `[]`), but v1 then panics unconditionally because `merkle_proof.is_empty()` is `true`.

The `From<ProofArgsV2> for ProofArgs` conversion preserves `merkle_proof` unchanged:

```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            ...
            merkle_proof: args.merkle_proof,
            ...
        }
    }
}
``` [5](#0-4) 

There is no code path in v2 that short-circuits before reaching v1's empty-proof guard.

---

### Impact Explanation

Any unprivileged NEAR caller invoking `verify_transaction_inclusion_v2` for a transaction in a single-transaction block receives a contract panic, even when the supplied proof is cryptographically valid. Downstream contracts or bridge applications that gate fund releases on a `true` return value from this function are permanently blocked for those blocks. The proof result is corrupted: a valid inclusion proof is rejected as if it were malformed.

---

### Likelihood Explanation

Single-transaction blocks (coinbase only) exist in the real Bitcoin chain, particularly in early history and during periods of very low mempool activity. Any relayer that has ingested such a block into the light client creates a reachable trigger. Likelihood is low but non-zero and fully attacker-independent — any legitimate caller supplying a correct proof for such a block hits the bug.

---

### Recommendation

In `verify_transaction_inclusion_v2`, add an early-return path for the single-transaction case before delegating to v1:

```rust
if args.merkle_proof.is_empty() {
    // Single-transaction block: merkle root == tx_id, already validated above via coinbase proof.
    return args.tx_id == header.block_header.merkle_root;
}
```

Alternatively, remove the `require!(!args.merkle_proof.is_empty(), ...)` guard from v1 and let `compute_root_from_merkle_proof` handle the empty-proof case (it already does so correctly).

---

### Proof of Concept

1. Identify any Bitcoin block with exactly one transaction (e.g., block 0 or any early mainnet block with only a coinbase). Its `merkle_root` equals the coinbase txid.
2. Submit that block header to the contract via `submit_blocks`.
3. Call `verify_transaction_inclusion_v2` with:
   - `tx_id` = coinbase txid
   - `tx_block_blockhash` = that block's hash
   - `tx_index` = 0
   - `merkle_proof` = `[]`
   - `coinbase_tx_id` = coinbase txid
   - `coinbase_merkle_proof` = `[]`
   - `confirmations` = 1
4. The coinbase proof check in v2 passes (`coinbase_tx_id == merkle_root`). Execution reaches v1's `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` and panics, despite the proof being valid.

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
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
