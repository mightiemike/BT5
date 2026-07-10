### Title
Repeated-Sibling Phantom-Index Bypass in `compute_root_from_merkle_proof` Allows `verify_transaction_inclusion` to Return `true` for a Nonexistent Transaction — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position`. In any Bitcoin block whose transaction count is odd, the last real transaction hash is duplicated as its own sibling. An attacker can supply the same `(tx_id, merkle_proof)` pair with `tx_index = N` (real, last slot) **and** `tx_index = N+1` (phantom duplicate slot) and both calls to `verify_transaction_inclusion` return `true`. The phantom index `N+1` does not correspond to any real on-chain transaction.

The stale-block variant of the attack is fully blocked: `mainchain_header_to_height.get(&args.tx_block_blockhash)` panics if the block is not in the current canonical chain. However, the phantom-index variant requires only a canonical block with an odd transaction count, which is the common case on mainnet.

---

### Finding Description

**`compute_root_from_merkle_proof` — no position bounds check** [1](#0-0) 

The function iterates over `merkle_proof`, using `current_position % 2` to decide left/right placement and `current_position /= 2` to ascend. There is no check that `transaction_position` is less than the number of leaves at any level.

**Bitcoin odd-width duplication**

`merkle_proof_calculator` duplicates the last hash when the level width is odd: [2](#0-1) 

For a 3-transaction block `[A, B, C]`, the level-0 padded array is `[A, B, C, C]`. The canonical proof for C at index 2 is `[C, hash(A,B)]`.

**Phantom index arithmetic**

Running `compute_root_from_merkle_proof` with `tx_id = C`, `tx_index = 3`, `proof = [C, hash(A,B)]`:

| Step | position | operation | result |
|------|----------|-----------|--------|
| 1 | 3 (odd) | `hash(proof[0]=C, current=C)` | `hash(C,C)` |
| 2 | 1 (odd) | `hash(proof[1]=hash(A,B), current=hash(C,C))` | root |

This is identical to the computation for index 2. Both return the stored `merkle_root`.

**`verify_transaction_inclusion` passes `tx_index` directly without bounds validation** [3](#0-2) 

`tx_index` is caller-controlled (`u64` in `ProofArgs`) and is cast to `usize` with no upper-bound check against the block's actual transaction count. [4](#0-3) 

**Canonical-chain guard does NOT help here**

The guard at lines 298–301 only rejects blocks absent from `mainchain_header_to_height`. The attacker uses a real canonical block — the guard passes. [5](#0-4) 

**`verify_transaction_inclusion_v2` does not fix this**

v2 adds a coinbase-proof check, then delegates to the deprecated v1 path via `self.verify_transaction_inclusion(args.into())`. The phantom-index bypass survives in v2 for any non-coinbase transaction. [6](#0-5) 

---

### Impact Explanation

A downstream bridge, unlock, or mint contract that:
1. calls `verify_transaction_inclusion` (or v2) to gate an asset release, and
2. deduplicates processed events by `(tx_id, tx_index)` rather than `tx_id` alone

can be called twice for the same on-chain transaction: once with the real index `N` and once with the phantom index `N+1`. Both calls return `true`. The attacker receives two asset releases for one Bitcoin transaction — a cross-chain double-spend.

Even a contract that deduplicates by `tx_id` alone is not automatically safe: if the attacker controls a transaction at the phantom slot (e.g., a transaction whose hash happens to equal the duplicate of the last real tx), the verifier accepts it as proven without it ever appearing on-chain.

---

### Likelihood Explanation

- Odd transaction counts are the norm on Bitcoin mainnet (most blocks have an odd number of transactions).
- The call is fully permissionless — no role, stake, or privileged key is required.
- The attacker only needs to observe a canonical block with an odd transaction count and know the last transaction's hash (public on-chain data).
- The deprecated function remains callable; nothing in the contract prevents its use.

---

### Recommendation

1. **In `compute_root_from_merkle_proof`**: reject or canonicalize any `transaction_position` that equals or exceeds `2^(proof.len())` — i.e., assert `transaction_position < (1 << merkle_proof.len())` before the loop. This eliminates phantom slots entirely.

2. **In `verify_transaction_inclusion` / v2**: require that `tx_index < 2^(merkle_proof.len())` and, if the block's transaction count is available, that `tx_index < tx_count`.

3. **Downstream guidance**: document that callers must deduplicate by `tx_id` alone (not `(tx_id, tx_index)`), since the same `tx_id` at two different indices is the canonical form of this attack.

---

### Proof of Concept

```
Block with 3 transactions: [A, B, C]
Padded tree:               [A, B, C, C]
Level-1:                   [hash(A,B), hash(C,C)]
Root:                      hash(hash(A,B), hash(C,C))

Canonical proof for C at index 2:
  proof = [C, hash(A,B)]

Call 1 (real):
  verify_transaction_inclusion({
    tx_id: C, tx_block_blockhash: <canonical block>, tx_index: 2,
    merkle_proof: [C, hash(A,B)], confirmations: 1
  })
  → compute_root_from_merkle_proof(C, 2, [C, hash(A,B)])
    step 1: pos=2 (even) → hash(C, C)       = hash(C,C)
    step 2: pos=1 (odd)  → hash(hash(A,B), hash(C,C)) = root  ✓
  → returns true

Call 2 (phantom — tx at index 3 does not exist):
  verify_transaction_inclusion({
    tx_id: C, tx_block_blockhash: <same canonical block>, tx_index: 3,
    merkle_proof: [C, hash(A,B)], confirmations: 1
  })
  → compute_root_from_merkle_proof(C, 3, [C, hash(A,B)])
    step 1: pos=3 (odd)  → hash(C, C)       = hash(C,C)
    step 2: pos=1 (odd)  → hash(hash(A,B), hash(C,C)) = root  ✓
  → returns true   ← nonexistent transaction accepted
```

Both calls pass the canonical-chain guard (same real block hash), pass the confirmation check, and return `true`. A bridge contract processing both events would release assets twice for a single Bitcoin transaction.

### Citations

**File:** merkle-tools/src/lib.rs (L9-11)
```rust
    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
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

**File:** contract/src/lib.rs (L298-301)
```rust
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
```

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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
