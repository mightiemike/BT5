### Title
Odd-Width Merkle Tree Phantom-Index Acceptance Enables Proof Reuse — (`merkle-tools/src/lib.rs` + `contract/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position`. In any Bitcoin block whose transaction count is odd, the last real leaf is duplicated by the tree-building convention. This makes the proof for the last real transaction (index `n`) produce an identical root when supplied with index `n+1` (a phantom slot that contains no real transaction). Because `verify_transaction_inclusion` also performs no bounds check on `tx_index`, an unprivileged caller can present the same `merkle_proof` twice — once for the real index and once for the phantom index — and the contract returns `true` for both calls.

---

### Finding Description

**`merkle_proof_calculator` (merkle-tools/src/lib.rs, lines 4–31)**

When the current level has an odd number of hashes, the last hash is duplicated before pairing: [1](#0-0) 

This means the sibling pushed into the proof for the last real leaf (index `n`) is `current_hashes[n]` — the leaf itself. The proof for the phantom slot at index `n+1` is computed identically, producing the same byte sequence.

**`compute_root_from_merkle_proof` (merkle-tools/src/lib.rs, lines 34–52)**

The verifier iterates the proof array and branches solely on `current_position % 2`: [2](#0-1) 

There is no check that `transaction_position` is within the range `[0, tx_count)`. The function accepts any non-negative integer.

**Concrete arithmetic for a 3-transaction block `[T0, T1, T2]`:**

| Step | Real path (index 2) | Phantom path (index 3) |
|------|---------------------|------------------------|
| Start | `cur = T2`, `pos = 2` | `cur = T2`, `pos = 3` |
| Round 1 | `pos % 2 == 0` → `cur = H(T2, proof[0]=T2)` = `H22` | `pos % 2 == 1` → `cur = H(proof[0]=T2, T2)` = `H22` |
| Round 2 | `pos=1`, odd → `cur = H(proof[1]=H01, H22)` = root | `pos=1`, odd → `cur = H(proof[1]=H01, H22)` = root |

Both paths produce the same root with the same proof `[T2, H(T0,T1)]`.

**`verify_transaction_inclusion` (contract/src/lib.rs, lines 288–323)**

The function checks confirmations and canonical-chain membership, but passes `tx_index` directly to `compute_root_from_merkle_proof` without any upper-bound validation: [3](#0-2) 

The contract stores only the Merkle root — not the transaction count — so it has no stored value to compare `tx_index` against. The check `!args.merkle_proof.is_empty()` is the only structural guard: [4](#0-3) 

**`verify_transaction_inclusion_v2` (contract/src/lib.rs, lines 347–369)**

The v2 function adds a coinbase-proof length check and a coinbase root check, but then delegates to the deprecated v1 function: [5](#0-4) 

The coinbase proof validates that index 0 is correct; it does not constrain the upper bound of `tx_index` for the target transaction. The phantom-index acceptance is therefore present in both API versions.

---

### Impact Explanation

A downstream bridge, unlock, or mint contract that uses `(tx_id, tx_index, block_hash)` as its replay-prevention key — a natural choice when a single transaction hash could appear at multiple positions — will accept two distinct proof submissions for the same on-chain event:

1. Legitimate user submits `(T2, block, index=2, proof=[T2, H01])` → `true` → payout issued.
2. Attacker submits `(T2, block, index=3, proof=[T2, H01])` → `true` → second payout issued.

The attacker needs no privileged role, no relayer key, and no hash collision. The only prerequisite is a Bitcoin block with an odd transaction count (the majority of mainnet blocks qualify) and knowledge of the last transaction's proof (publicly derivable from the block).

---

### Likelihood Explanation

- Odd-width Bitcoin blocks are the common case, not the exception.
- The proof for the last transaction is publicly available from any Bitcoin full node.
- The call is entirely permissionless (`verify_transaction_inclusion` has no `#[private]` or `#[trusted_relayer]` guard).
- The attacker does not need to submit any block headers; they only need to wait for a canonical block to accumulate the required confirmations. [6](#0-5) 

---

### Recommendation

1. **Store transaction count in the block header record**, or require callers to supply it and verify `tx_index < tx_count` inside `verify_transaction_inclusion`.
2. Alternatively, **reject any proof whose first sibling equals `tx_id`** when `tx_index` is even (the only case where a legitimate last-leaf proof has a self-sibling at the first level). This is a narrower heuristic but catches the phantom-index case without storing extra state.
3. Downstream consumers should use `tx_id` alone (not `tx_index`) as the replay-prevention key, since `tx_id` is the same for both the real and phantom slots.

---

### Proof of Concept

```rust
// 3-transaction block: [T0, T1, T2]
// Merkle root = H(H(T0,T1), H(T2,T2))
// Proof for T2 at index 2: [T2, H(T0,T1)]

let proof = vec![T2.clone(), h01.clone()];

// Call 1 – legitimate
let r1 = compute_root_from_merkle_proof(T2.clone(), 2, &proof);
assert_eq!(r1, merkle_root);   // true

// Call 2 – phantom index, same proof, same tx_id
let r2 = compute_root_from_merkle_proof(T2.clone(), 3, &proof);
assert_eq!(r2, merkle_root);   // also true — same root, no error
```

Both `verify_transaction_inclusion` calls with `tx_index=2` and `tx_index=3` return `true` against the same confirmed canonical block, allowing a downstream system to be triggered twice for a single Bitcoin transaction. [7](#0-6) [8](#0-7)

### Citations

**File:** merkle-tools/src/lib.rs (L10-11)
```rust
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

**File:** contract/src/lib.rs (L287-323)
```rust
    #[pause]
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
