### Title
Deprecated `verify_transaction_inclusion` Remains Callable Without Coinbase Merkle Proof Guard, Enabling 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, unprivileged public entry point on the NEAR contract. `verify_transaction_inclusion_v2` introduced a coinbase Merkle proof length check specifically to block the 64-byte transaction forgery attack, but v1 was never removed or access-gated — only annotated `#[deprecated]`. Any NEAR caller can bypass the coinbase guard entirely by calling v1 directly, obtaining a `true` result for a fabricated transaction inclusion proof.

---

### Finding Description

`verify_transaction_inclusion_v2` adds a mandatory coinbase Merkle proof validation step before delegating to v1:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "..."
);
// validates coinbase proof matches block merkle root
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(), 0usize, &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [1](#0-0) 

v1 itself performs no such check. It accepts a `tx_id` and a `merkle_proof`, computes the Merkle root, and returns `true` if it matches the stored block header's `merkle_root`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

The only guards on v1 are a confirmations bound check and an empty-proof check — neither prevents the 64-byte forgery. The `#[deprecated]` Rust attribute is a compiler lint; it does not restrict runtime callability. The function remains `pub` and is gated only by `#[pause]`, which is inactive by default. [3](#0-2) 

The 64-byte forgery (documented at https://www.bitmex.com/blog/64-Byte-Transactions) works because a 64-byte blob is simultaneously a valid serialized internal Merkle tree node (two 32-byte child hashes) and a plausible transaction hash input. An attacker who controls a real Bitcoin block can identify an internal node `N` at depth `d`, supply `N`'s hash as `tx_id` with a valid sibling path, and `compute_root_from_merkle_proof` will reconstruct the correct Merkle root — returning `true` for a transaction that does not exist.

---

### Impact Explanation

Any NEAR contract or off-chain application that calls `verify_transaction_inclusion` (v1) to gate a state transition — e.g., a bridge unlocking funds, a cross-chain swap releasing tokens, or a settlement contract crediting a payment — will accept a fabricated proof as valid. The attacker receives the downstream benefit (unlocked funds, credited payment) without having made the corresponding Bitcoin transaction. The corrupted value is the boolean proof result stored or acted upon by the consuming contract.

---

### Likelihood Explanation

The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion` directly. The 64-byte forgery technique is publicly documented and requires only a real Bitcoin block with a suitable internal node — a condition trivially satisfied on mainnet. Consuming contracts that have not yet migrated to v2 remain permanently exposed. The attack is realistic for any bridge or SPV-verification consumer that integrated against v1 before v2 was introduced.

---

### Recommendation

1. **Remove or hard-disable v1**: Replace the `#[deprecated]` annotation with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")` body, or delete the public function entirely and keep only the internal helper used by v2.
2. **If removal is not immediately feasible**, add the same coinbase Merkle proof validation to v1's body so both paths enforce the guard.
3. Audit all known consumers of `verify_transaction_inclusion` and migrate them to `verify_transaction_inclusion_v2`.

---

### Proof of Concept

1. Identify a real Bitcoin block `B` with Merkle root `R` that is stored in the light client's `headers_pool`.
2. Find an internal Merkle tree node `N` at depth `d` in `B`'s transaction tree. `N` is the double-SHA256 of two 32-byte child hashes — a 64-byte preimage.
3. Construct a sibling path `proof` of length `d` from `N` up to `R`.
4. Call `verify_transaction_inclusion` with:
   - `tx_id = hash(N)` (the internal node's hash, presented as a "transaction ID")
   - `tx_block_blockhash = B`
   - `tx_index` = position of `N` in the tree at depth `d`
   - `merkle_proof = proof`
   - `confirmations = 1`
5. `compute_root_from_merkle_proof(hash(N), index, proof)` reconstructs `R` exactly, so the function returns `true` — falsely asserting that `hash(N)` is an included transaction. [4](#0-3) [5](#0-4)

### Citations

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
