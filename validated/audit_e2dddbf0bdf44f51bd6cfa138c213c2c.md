### Title
Out-of-Bounds `tx_index` Bypasses Merkle Position Validation — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` contains no bounds check relating `tx_index` to the proof length or to the actual number of transactions in the block. An unprivileged NEAR caller can supply an out-of-range `tx_index` (e.g., `4` for a 4-tx block) paired with a proof whose elements are identical to those of a valid in-range index (e.g., `0`), because both indices traverse the same left-branch path through the tree. The function returns the correct merkle root, and `verify_transaction_inclusion` returns `true` for a position that does not correspond to any real transaction.

---

### Finding Description

`compute_root_from_merkle_proof` iterates over `merkle_proof` elements, using `current_position % 2` to decide left/right branching and `current_position /= 2` to ascend: [1](#0-0) 

There is no assertion that `transaction_position < 2^(merkle_proof.len())`, nor any check that `transaction_position` is within the actual leaf count of the committed block. The contract stores only the `merkle_root` in block headers — it never records the transaction count — so no downstream guard can recover the missing bound. [2](#0-1) 

**Why `tx_index = 4` aliases `tx_index = 0` for a depth-2 proof:**

For a 4-tx tree the proof has length 2. Tracing both indices through the loop:

| Level | `tx_index=0` position | branch | `tx_index=4` position | branch |
|-------|----------------------|--------|-----------------------|--------|
| 0     | 0 (even)             | left   | 4 (even)              | left   |
| 1     | 0 (even)             | left   | 2 (even)              | left   |

Both consume the same `proof[0]` and `proof[1]` on the same side, producing an identical hash chain and therefore the same root. Any `tx_index` of the form `N + 2^k` (where `N` is a valid index and `k` equals the proof length) aliases `N`'s path identically.

`verify_transaction_inclusion` then compares the computed root to `header.block_header.merkle_root` and returns `true`: [3](#0-2) 

`verify_transaction_inclusion_v2` inherits the same flaw because it delegates to `verify_transaction_inclusion` after the coinbase check: [4](#0-3) 

The `ProofArgs` struct accepts `tx_index: u64` with no range constraint: [5](#0-4) 

---

### Impact Explanation

`verify_transaction_inclusion` returns `true` for a `(tx_id, tx_index)` pair where `tx_index` is outside the valid leaf range of the block. Any downstream NEAR contract that relies on this call to gate a payment, bridge withdrawal, or state transition can be deceived into accepting a proof that asserts a transaction exists at a position that does not exist in the committed block. This matches the Critical scope item: *wrong index* inclusion claim accepted as valid.

---

### Likelihood Explanation

The call is a public, unprivileged NEAR contract method (gated only by the `#[pause]` flag, not by any role). Any account can submit arbitrary `ProofArgs`. No cryptographic work is required: the attacker reuses the legitimate proof elements for index 0 verbatim and changes only the `tx_index` field. The attack is deterministic and requires no brute force.

---

### Recommendation

Add an explicit upper-bound check inside `compute_root_from_merkle_proof` (or at the call site in `verify_transaction_inclusion`) asserting that `transaction_position < (1usize << merkle_proof.len())`. This ensures that any `tx_index` whose binary representation has more significant bits than the proof depth is rejected before the loop executes:

```rust
assert!(
    transaction_position < (1usize << merkle_proof.len()),
    "tx_index out of range for the given proof depth"
);
```

Because the contract does not store transaction counts, this is the tightest bound enforceable from the proof length alone. It eliminates all aliasing attacks of the form `tx_index = valid_index + 2^k`.

---

### Proof of Concept

Rust unit test (drop into `merkle-tools/src/lib.rs` test module, no external dependencies):

```rust
#[test]
fn test_out_of_range_tx_index_aliases_index_zero() {
    // 4-tx tree, depth 2
    let tx0 = decode_hex("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    let tx1 = decode_hex("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    let tx2 = decode_hex("cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc");
    let tx3 = decode_hex("dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd");

    let txs = vec![tx0.clone(), tx1.clone(), tx2.clone(), tx3.clone()];
    let real_root = merkle_root_calculator(&txs);

    // Legitimate proof for index 0
    let proof_for_0 = merkle_proof_calculator(txs, 0);
    assert_eq!(proof_for_0.len(), 2);

    // Valid: index 0 with its own proof
    let root_at_0 = compute_root_from_merkle_proof(tx0.clone(), 0, &proof_for_0);
    assert_eq!(root_at_0, real_root);

    // Attack: index 4 (out of range for a 4-tx block) with the same proof
    let root_at_4 = compute_root_from_merkle_proof(tx0.clone(), 4, &proof_for_0);

    // Both return the same root — the function accepts the out-of-range index
    assert_eq!(root_at_4, real_root,
        "VULNERABILITY: tx_index=4 (nonexistent) produces the correct root");
}
```

The final `assert_eq!` passes on unmodified production code, confirming that `verify_transaction_inclusion` would return `true` for `tx_index = 4` in a 4-transaction block. [6](#0-5) [7](#0-6)

### Citations

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

**File:** contract/src/lib.rs (L288-323)
```rust
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

**File:** contract/src/lib.rs (L367-368)
```rust
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
