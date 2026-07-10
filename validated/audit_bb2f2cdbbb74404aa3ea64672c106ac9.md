### Title
Empty Merkle Proof Rejected for Single-Transaction Blocks — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` enforces a strict invariant (`!args.merkle_proof.is_empty()`) that is mathematically invalid for a known, legitimate Bitcoin scenario: a block containing only one transaction (the coinbase). For such blocks the merkle root *is* the transaction hash, so the correct and only valid proof is an empty proof. The contract panics instead of accepting it, permanently blocking SPV verification for any transaction in a single-transaction block.

### Finding Description

In `contract/src/lib.rs` the public entry point `verify_transaction_inclusion` contains:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

Immediately after, it calls `compute_root_from_merkle_proof`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

`compute_root_from_merkle_proof` is defined as:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    for proof_hash in merkle_proof { ... }
    current_hash
}
``` [3](#0-2) 

When `merkle_proof` is empty the loop body never executes and the function correctly returns `transaction_hash` unchanged. For a single-transaction block `merkle_root == tx_hash`, so the comparison would return `true` — but the `require!` guard panics before that comparison is ever reached.

The newer `verify_transaction_inclusion_v2` does not fix this. It first validates the coinbase proof (which also passes with an empty proof for a single-tx block), then delegates to the deprecated v1 via `self.verify_transaction_inclusion(args.into())`, hitting the same guard:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [4](#0-3) 

The length-equality pre-check in v2 (`merkle_proof.len() == coinbase_merkle_proof.len()`) forces both proofs to be empty simultaneously for a single-tx block, so the caller cannot work around the guard by padding one of them. [5](#0-4) 

### Impact Explanation

Any downstream NEAR contract or user that submits a valid SPV proof for a transaction residing in a single-transaction Bitcoin block will receive a hard panic (`require!` failure) rather than `true`. Because the block is already stored in the `headers_pool` and the proof is mathematically correct, there is no alternative path to obtain a successful verification result. If a bridge, atomic-swap, or custody contract gates fund release on a `true` return from this function, those funds are permanently frozen — identical in character to the Illuminate Redeemer freezing funds when Sense PTs could not be redeemed due to an unhandled known-loss scenario.

### Likelihood Explanation

Single-transaction blocks (containing only the coinbase) are rare on mainnet today but are a well-known, protocol-valid Bitcoin construct. They appeared frequently in early Bitcoin history and can still occur. A relayer submitting such a block to the contract is a normal, unprivileged operation. Once the block is stored, any caller invoking `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` with the correct (empty) proof triggers the panic. No special privileges, leaked keys, or social engineering are required.

### Recommendation

Remove the blanket `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard. The mathematical check that follows (`compute_root_from_merkle_proof(...) == header.block_header.merkle_root`) already handles the empty-proof case correctly and is the authoritative validation. If a guard is still desired for defence-in-depth, restrict it to cases where `tx_index != 0` or where the block's stored merkle root is known to differ from the submitted `tx_id`.

### Proof of Concept

1. A relayer submits a valid Bitcoin block whose only transaction is the coinbase (merkle_root = coinbase_txid). The block is accepted and stored in `headers_pool`.
2. A user calls `verify_transaction_inclusion_v2` with:
   - `tx_id` = coinbase txid
   - `tx_index` = 0
   - `merkle_proof` = `[]` (empty — correct for depth-0 tree)
   - `coinbase_tx_id` = coinbase txid
   - `coinbase_merkle_proof` = `[]` (must match length per the v2 pre-check)
3. The coinbase proof check passes: `compute_root_from_merkle_proof(coinbase_txid, 0, &[])` returns `coinbase_txid` == `merkle_root`. ✓
4. `verify_transaction_inclusion` is called. `require!(!args.merkle_proof.is_empty())` fires → **panic**. ✗
5. The mathematically valid proof is permanently rejected; any downstream contract awaiting `true` is blocked.

### Citations

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

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
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
