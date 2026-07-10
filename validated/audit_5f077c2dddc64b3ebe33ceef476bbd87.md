### Title
Merkle Internal-Node Forgery in `verify_transaction_inclusion` Returns `true` for Non-Existent Transactions — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` passes the caller-supplied `tx_id` directly into `compute_root_from_merkle_proof` with no check that it is a leaf (actual transaction) rather than an internal Merkle tree node. An attacker who knows a block's Merkle tree structure can supply an internal node hash as `tx_id` with a shortened proof path and cause the function to reconstruct the correct `merkle_root`, returning `true` for a transaction that does not exist in the block.

---

### Finding Description

`compute_root_from_merkle_proof` is a pure hash-chaining function with no leaf/internal-node distinction: [1](#0-0) 

It accepts any 32-byte value as the starting hash and iterates over the proof path, combining hashes level by level. There is no domain separation between leaf hashes and internal node hashes.

`verify_transaction_inclusion` feeds the caller-controlled `args.tx_id` directly into this function and compares the result to the stored `merkle_root`: [2](#0-1) 

The code itself acknowledges this in its `# Warning` doc comment: [3](#0-2) 

Despite this acknowledgment, the function remains `pub` and callable. The `#[deprecated]` attribute is a Rust compile-time hint only — it imposes no runtime restriction on NEAR contract callers. The `#[pause]` gate is not engaged by default. [4](#0-3) 

---

### Impact Explanation

For a 4-leaf Merkle tree with leaves `[A, B, C, D]`:

- Level 1 internal node: `AB = hash(A, B)`
- Root: `hash(AB, CD)`

An attacker submits:
- `tx_id = AB` (internal node, not a real transaction)
- `tx_index = 0`
- `merkle_proof = [CD]`

`compute_root_from_merkle_proof(AB, 0, [CD])` computes `hash(AB, CD)` = root → returns `true`.

Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate fund releases or state transitions will be deceived into acting on a fabricated transaction.

---

### Likelihood Explanation

The block's Merkle tree structure is fully public (derivable from the block's transaction list). The attacker needs only:
1. A block in `mainchain_header_to_height` with sufficient confirmations (publicly observable state).
2. Knowledge of any internal node hash (trivially computed from public transaction data).
3. The ability to call a public NEAR contract method.

No privileged role, leaked key, or social engineering is required.

---

### Recommendation

1. **Remove or permanently disable `verify_transaction_inclusion` (v1).** It is already deprecated; the `#[pause]` gate should be activated and the function should be removed in the next upgrade.
2. **In `verify_transaction_inclusion_v2`**, add an explicit leaf-depth check: require that `merkle_proof.len()` corresponds to the expected tree depth derived from the coinbase proof, and verify that `tx_index` is within the valid leaf range `[0, 2^depth)`.
3. **Apply domain separation** in `compute_root_from_merkle_proof` by tagging leaf inputs (e.g., `SHA256d(0x00 || leaf)`) vs. internal nodes (e.g., `SHA256d(0x01 || left || right)`), consistent with RFC 6962 / Certificate Transparency practice.

---

### Proof of Concept

```rust
// In merkle-tools or a contract integration test:
use merkle_tools::{compute_root_from_merkle_proof, H256};

fn double_sha256_pair(a: &H256, b: &H256) -> H256 { /* ... */ }

let leaf_a: H256 = /* tx hash A */;
let leaf_b: H256 = /* tx hash B */;
let leaf_c: H256 = /* tx hash C */;
let leaf_d: H256 = /* tx hash D */;

let ab = double_sha256_pair(&leaf_a, &leaf_b);  // internal node
let cd = double_sha256_pair(&leaf_c, &leaf_d);  // internal node
let root = double_sha256_pair(&ab, &cd);

// Attacker submits internal node `ab` as if it were a leaf at index 0
// with a 1-element proof [cd]:
let forged_root = compute_root_from_merkle_proof(ab.clone(), 0, &vec![cd]);
assert_eq!(forged_root, root);  // passes — forgery succeeds
```

This directly demonstrates that `verify_transaction_inclusion` returns `true` for `tx_id = ab` (an internal node), even though `ab` is not a transaction in the block. [5](#0-4) [6](#0-5)

### Citations

**File:** merkle-tools/src/lib.rs (L34-51)
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
```

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
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
