### Title
Merkle Index Aliasing in `compute_root_from_merkle_proof` Allows False Inclusion Proof at Phantom Position — (`merkle-tools/src/lib.rs`)

### Summary

`compute_root_from_merkle_proof` iterates exactly `proof.len()` times, using only the parity of `current_position` at each level. Because `(N + 2^L) >> k` has the same parity as `N >> k` for all `k < L` (since `2^(L-k)` is even), any index of the form `N + m·2^L` produces an identical traversal and identical root as index `N`. There is no upper-bound check on `tx_index` anywhere in the call chain, so an attacker who holds a valid proof for position `N` can call `verify_transaction_inclusion` with `tx_index = N + 2^proof_length` and receive `true`.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` determines left/right placement at each tree level solely by `current_position % 2`, then halves the position: [1](#0-0) 

The loop runs exactly `merkle_proof.len()` times. For a proof of length `L`, the only bits of `transaction_position` that influence the traversal are bits `0..L-1`. Bit `L` and above are never examined. Therefore, positions `N` and `N + 2^L` produce identical left/right decisions at every level and thus the same computed root.

`verify_transaction_inclusion` accepts `tx_index: u64`, casts it to `usize`, and passes it directly with no range check: [2](#0-1) 

`ProofArgs.tx_index` is declared as a plain `u64` with no validation constraint: [3](#0-2) 

`verify_transaction_inclusion_v2` does not fix this; it validates the coinbase proof at hardcoded index `0` and then delegates to `verify_transaction_inclusion` with the same unchecked `tx_index`: [4](#0-3) 

---

### Impact Explanation

A downstream bridge or settlement contract that uses `(tx_id, tx_index)` as a uniqueness key to prevent double-spend can be presented with the same transaction at two distinct claimed positions — `N` and `N + 2^L` — and the light client will return `true` for both. This constitutes false inclusion verification: the contract attests that a transaction exists at a position where it does not exist in the Bitcoin block's Merkle tree.

---

### Likelihood Explanation

The attacker only needs:
1. A valid Merkle proof for `tx_id` at real position `N` in a confirmed canonical block (publicly available from any Bitcoin node).
2. The ability to call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` — both are public NEAR view/call methods with no access control.

No privileged role, key leak, or social engineering is required.

---

### Recommendation

After the loop, assert that `current_position == 0`. A valid proof for a leaf at position `N` in a tree of depth `L` must reduce to the root, meaning `N >> L` must equal `0`. Any `tx_index >= 2^L` will leave `current_position > 0` after the loop and must be rejected:

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

    // Reject aliased indices: a valid leaf position must be fully consumed
    assert_eq!(current_position, 0, "tx_index out of range for proof length");

    current_hash
}
```

Alternatively, enforce `tx_index < 2^merkle_proof.len()` at the `verify_transaction_inclusion` entry point before calling `compute_root_from_merkle_proof`.

---

### Proof of Concept

For a 4-leaf tree (proof length = 2), position `0` and position `4 = 0 + 2^2` produce the same root:

```
Level 0: current_position=0, 0%2==0 → hash(tx, proof[0]); position→0
Level 1: current_position=0, 0%2==0 → hash(prev, proof[1]); position→0

Level 0: current_position=4, 4%2==0 → hash(tx, proof[0]); position→2
Level 1: current_position=2, 2%2==0 → hash(prev, proof[1]); position→1
```

Both traversals make identical left/right choices at every level, producing the same root. The call:

```
verify_transaction_inclusion(tx_id=H, tx_block_blockhash=B, tx_index=4, merkle_proof=proof_for_0, confirmations=0)
```

returns `true` even though `H` is not at position 4 in the block. [5](#0-4)

### Citations

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

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L347-368)
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
```

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
