I need to trace through the exact arithmetic of `merkle_proof_calculator` and `compute_root_from_merkle_proof` for an odd-length list to verify the claim before rendering a verdict.

**Trace for N=5 (odd), positions 4 and 5:**

`merkle_proof_calculator(txs, 4)` — Round 1:
- `len=5` odd → push `txs[4]` → `[tx0,tx1,tx2,tx3,tx4,tx4]`
- position=4 (even) → push `current_hashes[5]` = **tx4**
- new_hashes = `[h01, h23, h44]`, position → 2

Round 2:
- `len=3` odd → push `h44` → `[h01,h23,h44,h44]`
- position=2 (even) → push `current_hashes[3]` = **h44**
- new_hashes = `[hash(h01,h23), hash(h44,h44)]`, position → 1

Round 3:
- position=1 (odd) → push `current_hashes[0]` = **hash(h01,h23)**
- Proof = `[tx4, h44, hash(h01,h23)]`

`merkle_proof_calculator(txs, 5)` — Round 1:
- `len=5` odd → push `txs[4]` → `[tx0,tx1,tx2,tx3,tx4,tx4]`
- position=5 (odd) → push `current_hashes[4]` = **tx4** ← same element
- new_hashes = `[h01, h23, h44]`, position → 2

Rounds 2 and 3 are identical to above.
Proof = `[tx4, h44, hash(h01,h23)]` — **identical**.

`compute_root_from_merkle_proof(tx4, 4, proof)`:
- pos=4 (even): `hash(tx4, tx4)` = h44
- pos=2 (even): `hash(h44, h44)`
- pos=1 (odd): `hash(hash(h01,h23), hash(h44,h44))` = **root** ✓

`compute_root_from_merkle_proof(tx4, 5, proof)`:
- pos=5 (odd): `hash(proof[0]=tx4, tx4)` = `hash(tx4,tx4)` = h44 ← same result because both operands are tx4
- pos=2 (even): `hash(h44, h44)`
- pos=1 (odd): `hash(hash(h01,h23), hash(h44,h44))` = **root** ✓

The mathematical claim holds exactly. Now checking the on-chain path:

`verify_transaction_inclusion` passes `args.tx_index` directly to `compute_root_from_merkle_proof` with no upper-bound check: [1](#0-0) 

`verify_transaction_inclusion_v2` only adds a coinbase-proof length check and coinbase root check, then delegates to `verify_transaction_inclusion` unchanged: [2](#0-1) 

Neither function validates that `tx_index < number_of_transactions_in_block`. The block header stored on-chain contains only the `merkle_root`, not the transaction count, so no such check is possible without additional data. [3](#0-2) 

The root cause is in `merkle_proof_calculator`: when the list is odd-length, the last real element is duplicated, and both position `N-1` (even, pushes the duplicate at index N) and position `N` (odd, pushes the real element at index N-1) push the same hash value, making the proof vectors byte-for-byte identical. [4](#0-3) 

`compute_root_from_merkle_proof` then produces the same root for both positions because the first proof step hashes `tx4` against itself regardless of whether the position is even or odd (since both the transaction hash and the proof element are `tx4`). [5](#0-4) 

---

### Title
Phantom Duplicate-Leaf Index Bypass: `verify_transaction_inclusion` Returns `true` for Non-Existent `tx_index = N` in Odd-Length Blocks — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

### Summary
For any block with an odd number of transactions N, `merkle_proof_calculator` produces byte-identical proofs for positions `N-1` (the real last transaction) and `N` (a non-existent phantom index). `compute_root_from_merkle_proof` accepts both positions and returns the same valid Merkle root. Because `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` perform no upper-bound check on `tx_index`, an attacker can call either function with `tx_index = N` and the proof generated for `tx_index = N-1`, and the contract returns `true` for a transaction index that does not exist in the block.

### Finding Description
`merkle_proof_calculator` duplicates the last element when the list length is odd: [6](#0-5) 

For position `N-1` (even): the sibling pushed is `current_hashes[N]`, which is the duplicate of `txs[N-1]`.
For position `N` (odd): the sibling pushed is `current_hashes[N-1]`, which is also `txs[N-1]`.
Both push the same hash. All subsequent rounds are identical, so the full proof vectors are equal.

In `compute_root_from_merkle_proof`, the first step for position `N-1` (even) computes `hash(tx[N-1], proof[0])` = `hash(tx[N-1], tx[N-1])`, and for position `N` (odd) computes `hash(proof[0], tx[N-1])` = `hash(tx[N-1], tx[N-1])` — identical because both operands are the same value. [7](#0-6) 

The on-chain verifier has no guard against an out-of-range index: [8](#0-7) 

### Impact Explanation
An attacker who knows the valid proof for the real last transaction in any odd-length block can call `verify_transaction_inclusion` (or `_v2`) with `tx_index = N` and receive `true`. Any protocol built on top of this contract that tracks claimed transactions by `(tx_id, tx_index)` pairs — rather than by `tx_id` alone — can be double-claimed: once at index `N-1` and once at the phantom index `N`, releasing funds or credits twice for a single on-chain transaction.

### Likelihood Explanation
- Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, permissionless NEAR contract calls (only gated by the `#[pause]` flag, which is off in normal operation).
- Roughly half of all Bitcoin/Litecoin/Dogecoin blocks have an odd transaction count, so the precondition is met frequently.
- The attacker needs only the standard Merkle proof for the last transaction, which is publicly derivable from any block explorer.
- No privileged role, key, or social engineering is required.

### Recommendation
Add an explicit upper-bound check in `verify_transaction_inclusion` requiring `tx_index < total_tx_count`. Because the block header does not store the transaction count, the count must be passed as an additional argument and committed to (e.g., by including it in the coinbase proof or a separate authenticated field). Alternatively, reject any `tx_index` that is even and equal to the last index when the proof's first element equals the transaction hash itself (detecting the self-pairing), though passing the count is the cleaner fix.

### Proof of Concept
```rust
// In merkle-tools, parameterized over odd N in [3,5,7,9]:
let txs: Vec<H256> = (0..N).map(|i| make_hash(i)).collect();
let proof_last  = merkle_proof_calculator(txs.clone(), N - 1);
let proof_ghost = merkle_proof_calculator(txs.clone(), N);
assert_eq!(proof_last, proof_ghost);  // identical proofs

let root = compute_root_from_merkle_proof(txs[N-1].clone(), N - 1, &proof_last);
let root_ghost = compute_root_from_merkle_proof(txs[N-1].clone(), N, &proof_ghost);
assert_eq!(root, root_ghost);  // same root → both pass on-chain check
```

Call sequence:
1. Off-chain: `merkle_proof_calculator(block_txs, N-1)` → `proof`
2. On-chain (legitimate): `verify_transaction_inclusion({tx_id: tx[N-1], tx_index: N-1, merkle_proof: proof, ...})` → `true`
3. On-chain (exploit): `verify_transaction_inclusion({tx_id: tx[N-1], tx_index: N, merkle_proof: proof, ...})` → `true` (phantom index, same proof, same tx_id)

### Citations

**File:** contract/src/lib.rs (L310-322)
```rust
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

**File:** merkle-tools/src/lib.rs (L9-18)
```rust
    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
        }

        if transaction_position % 2 == 1 {
            merkle_proof.push(current_hashes[transaction_position - 1].clone());
        } else {
            merkle_proof.push(current_hashes[transaction_position + 1].clone());
        }
```

**File:** merkle-tools/src/lib.rs (L42-49)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }
```
