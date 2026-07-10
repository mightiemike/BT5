### Title
Phantom Duplicate-Leaf Index Bypass in `verify_transaction_inclusion_v2` — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` returns `true` for a claimed `tx_index = N` (a phantom position that does not exist) in any Zcash block with an odd number of transactions `N`, because `compute_root_from_merkle_proof` is purely positional and no guard checks that `tx_index < N`. The duplicate-leaf padding used to build the merkle tree makes the phantom index arithmetically equivalent to the real last index.

---

### Finding Description

When a block has an odd number of transactions `N`, the merkle tree builder duplicates the last leaf `tx[N-1]` to form a pair. `merkle_proof_calculator` for `tx_index = N-1` therefore emits `tx[N-1]` itself as the first sibling in the proof.

`compute_root_from_merkle_proof` is a pure positional computation:

```
for proof_hash in merkle_proof:
    if current_position % 2 == 0:
        current_hash = H(current_hash, proof_hash)
    else:
        current_hash = H(proof_hash, current_hash)
    current_position /= 2
``` [1](#0-0) 

For a 5-tx block, the proof for `tx_index=4` is `[tx4, H(tx4,tx4), H(H01,H23)]`. Tracing both calls:

| Step | `tx_index=4` (real) | `tx_index=5` (phantom) |
|------|---------------------|------------------------|
| init | hash=tx4, pos=4 | hash=tx4, pos=5 |
| 1 | pos=4 even → H(tx4,tx4)=H44, pos=2 | pos=5 odd → H(tx4,tx4)=H44, pos=2 |
| 2 | pos=2 even → H(H44,H44), pos=1 | pos=2 even → H(H44,H44), pos=1 |
| 3 | pos=1 odd → H(H01H23, H44H44) = root | pos=1 odd → H(H01H23, H44H44) = root |

Both produce the same merkle root. The phantom index 5 is indistinguishable from the real index 4 under this proof.

`verify_transaction_inclusion` performs no bounds check on `tx_index`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

The block header stores no transaction count, so the contract has no on-chain way to enforce `tx_index < N`.

`verify_transaction_inclusion_v2` adds a coinbase proof check, but that check is independent and does not constrain `tx_index`: [3](#0-2) 

---

### Impact Explanation

Any unprivileged NEAR caller can invoke `verify_transaction_inclusion_v2` with:
- `tx_id = tx[N-1]` (the real last transaction hash)
- `tx_index = N` (the phantom position)
- `merkle_proof` = the proof computed for `tx_index = N-1`
- `coinbase_tx_id` / `coinbase_merkle_proof` = a valid coinbase proof (same depth)

The function returns `true`, asserting that a transaction exists at index `N` in the block, which is false. Any downstream contract or protocol that trusts this return value to gate payments, bridge withdrawals, or state transitions can be deceived.

---

### Likelihood Explanation

- No privileged role is required; the function is a public `view`/`call` with no access control.
- The precondition (a canonical block with an odd transaction count) is common in practice.
- The attacker needs only the real block's merkle proof data, which is publicly derivable from the block.
- The coinbase-proof length constraint (`merkle_proof.len() == coinbase_merkle_proof.len()`) is automatically satisfied because both proofs have the same depth for the same block.

---

### Recommendation

Add an explicit upper-bound check on `tx_index`. Because the block header does not store the transaction count, the caller must supply it and the contract must verify it is consistent with the proof depth:

1. Add `tx_count: u64` to `ProofArgs` / `ProofArgsV2`.
2. Require `args.tx_index < args.tx_count`.
3. Require `args.tx_count` is consistent with the proof depth: `ceil(log2(tx_count)) == merkle_proof.len()` (or an equivalent tight bound).
4. Alternatively, require `tx_index` is strictly less than `2^(merkle_proof.len() - 1)` for the last level, which rules out the phantom slot without needing an explicit count.

---

### Proof of Concept

```rust
// 5-tx block: [tx0, tx1, tx2, tx3, tx4]
// merkle_root = H(H(H01,H23), H(H44,H44))
// proof for index 4 = [tx4, H(tx4,tx4), H(H01,H23)]

let proof_for_4 = merkle_proof_calculator(txs.clone(), 4);

// Real index: passes (expected)
let root_real = compute_root_from_merkle_proof(tx4.clone(), 4, &proof_for_4);
assert_eq!(root_real, merkle_root);

// Phantom index: also passes (the bug)
let root_phantom = compute_root_from_merkle_proof(tx4.clone(), 5, &proof_for_4);
assert_eq!(root_phantom, merkle_root); // TRUE — phantom tx at index 5 "verified"
``` [4](#0-3) [5](#0-4) 

The same proof bytes submitted to `verify_transaction_inclusion_v2` with `tx_index=5` pass all three checks (proof-length equality, coinbase proof, tx proof) and return `true` for a transaction that does not exist.

### Citations

**File:** merkle-tools/src/lib.rs (L4-31)
```rust
pub fn merkle_proof_calculator(tx_hashes: Vec<H256>, transaction_position: usize) -> Vec<H256> {
    let mut transaction_position = transaction_position;
    let mut merkle_proof = Vec::new();
    let mut current_hashes = tx_hashes;

    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
        }

        if transaction_position % 2 == 1 {
            merkle_proof.push(current_hashes[transaction_position - 1].clone());
        } else {
            merkle_proof.push(current_hashes[transaction_position + 1].clone());
        }

        let mut new_hashes = Vec::new();

        for i in (0..current_hashes.len() - 1).step_by(2) {
            new_hashes.push(compute_hash(&current_hashes[i], &current_hashes[i + 1]));
        }

        current_hashes = new_hashes;
        transaction_position /= 2;
    }

    merkle_proof
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
