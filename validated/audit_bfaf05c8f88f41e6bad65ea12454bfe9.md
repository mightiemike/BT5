### Title
`verify_transaction_inclusion` accepts phantom `tx_index` N for the duplicated last leaf in an odd-width Merkle tree, enabling double-spend — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

---

### Summary

`compute_root_from_merkle_proof` never validates that `transaction_position` is within the actual leaf count of the tree. Bitcoin's Merkle construction duplicates the last leaf when the leaf count is odd. This means the same proof bytes and the same `tx_id` produce the correct root for **two distinct index values** (N−1 and N). Because the contract stores no transaction count, it cannot reject the phantom index. Any downstream bridge that uses `(tx_id, tx_index)` as a replay-protection key will process the same deposit twice.

---

### Finding Description

**Root cause — `compute_root_from_merkle_proof`** [1](#0-0) 

The function iterates over `merkle_proof`, using `current_position % 2` to decide left/right placement and then halving the position. It performs **no bounds check** on `transaction_position`.

**Why the duplicate-leaf property creates two valid positions**

For a 3-tx block `[T0, T1, T2]`, Bitcoin pads to `[T0, T1, T2, T2]`:

```
Level 0 (leaves): T0   T1   T2   T2*   (* = duplicate)
Level 1:          H(T0,T1)  H(T2,T2)
Root:             H(H(T0,T1), H(T2,T2))
```

The canonical proof for T2 at **position 2** is `proof = [T2, H(T0,T1)]`.

Trace with `position = 2` (real):
```
step 1: pos=2 (even)  → hash = H(T2,  proof[0]=T2)  = H(T2,T2)
step 2: pos=1 (odd)   → hash = H(proof[1]=H(T0,T1), H(T2,T2)) = root ✓
```

Trace with `position = 3` (phantom):
```
step 1: pos=3 (odd)   → hash = H(proof[0]=T2, T2)  = H(T2,T2)  ← identical
step 2: pos=1 (odd)   → hash = H(proof[1]=H(T0,T1), H(T2,T2)) = root ✓
```

Both calls return the correct root. The function cannot distinguish them.

**The caller-facing entry point** [2](#0-1) 

`verify_transaction_inclusion` is public, not `#[private]`, not `#[trusted_relayer]`, and callable by any account. It passes the caller-supplied `args.tx_index` directly to `compute_root_from_merkle_proof` with no upper-bound check. [3](#0-2) 

The contract stores only `merkle_root` inside `ExtendedHeader`; it never stores the transaction count, so it has no data with which to reject an out-of-range index. [4](#0-3) 

---

### Impact Explanation

A downstream bridge that uses `(tx_id, tx_index)` as its replay-protection key will see two distinct keys — `(T2, N−1)` and `(T2, N)` — both verified as `true` by the light client. The attacker submits a single real deposit transaction T2 (at the last position of any odd-width Bitcoin block), then calls `verify_transaction_inclusion` a second time with `tx_index = N` (the phantom duplicate position) using the identical proof. The bridge has not seen `(T2, N)` before and mints/unlocks a second time. This is a direct fund-theft path.

---

### Likelihood Explanation

- The function is public and requires no special role.
- Odd-width transaction counts are common in real Bitcoin blocks (any block with an odd number of transactions qualifies).
- The attacker only needs to identify such a block already in the light client's mainchain, construct the standard last-leaf proof, and submit it with `tx_index = N` instead of `N−1`.
- No relayer compromise, no DAO access, no key leakage is required.

---

### Recommendation

1. **Enforce index bounds in `compute_root_from_merkle_proof`**: the maximum valid index for a proof of length `k` is `2^k − 1`; reject any `transaction_position ≥ 2^(merkle_proof.len())`.
2. **Stronger fix**: require callers to supply the total leaf count and validate `transaction_position < leaf_count`, then verify the proof length matches `ceil(log2(leaf_count))`.
3. Note that `verify_transaction_inclusion_v2` calls the deprecated `verify_transaction_inclusion` internally and inherits the same flaw. [5](#0-4) 

---

### Proof of Concept

```rust
// Odd-width tree: 3 transactions [T0, T1, T2]
// Bitcoin Merkle root = H(H(T0,T1), H(T2,T2))
//
// Canonical proof for T2 at index 2:
//   proof = [T2, H(T0,T1)]
//
// Same proof also satisfies index 3 (phantom):

let proof = vec![T2.clone(), hash_pair(&T0, &T1)];

// Real position — returns true (expected)
assert!(compute_root_from_merkle_proof(T2.clone(), 2, &proof) == merkle_root);

// Phantom position — also returns true (BUG)
assert!(compute_root_from_merkle_proof(T2.clone(), 3, &proof) == merkle_root);

// In the contract context:
// Call 1: verify_transaction_inclusion(tx_id=T2, tx_index=2, ...) → true  (real deposit)
// Call 2: verify_transaction_inclusion(tx_id=T2, tx_index=3, ...) → true  (phantom — BUG)
// Bridge mints twice for the same on-chain transaction.
```

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

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
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
