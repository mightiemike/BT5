### Title
`verify_transaction_inclusion` Incorrectly Rejects Valid Proofs for Single-Transaction Blocks — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` contains an unconditional guard `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` that is too broad. It rejects all empty Merkle proofs, but an empty proof is cryptographically valid when a block contains exactly one transaction — the Merkle root equals the transaction hash and no sibling hashes are needed. This is the direct analog of M-04: a guard condition that should carry an additional clause but instead causes all valid operations of a specific class to fail.

---

### Finding Description

In `verify_transaction_inclusion` (`contract/src/lib.rs`, line 315):

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

This check fires unconditionally for any empty `merkle_proof`. However, in Bitcoin (and all supported chains), when a block contains exactly one transaction, the Merkle tree has a single leaf. The Merkle root **is** the transaction hash, and the inclusion proof requires zero sibling hashes — the proof vector is legitimately empty.

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` already handles this correctly: when `merkle_proof` is empty, the loop body never executes and `current_hash` (the transaction hash) is returned unchanged. [1](#0-0) 

The guard at line 315 intercepts before the computation is ever attempted, causing a panic for every valid single-transaction-block proof. [2](#0-1) 

The same code path is reached from `verify_transaction_inclusion_v2` via `self.verify_transaction_inclusion(args.into())`, so both public entry points are affected. [3](#0-2) 

The `header` object (which holds `merkle_root`) is already in scope at the point of the check, so the fix requires no restructuring. [4](#0-3) 

---

### Impact Explanation

Any NEAR caller invoking `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a transaction in a single-transaction block will always receive a panic (`"Merkle proof is empty"`), regardless of whether the proof is mathematically correct. Downstream contracts or bridge logic that depend on this verification result will be permanently blocked for this class of blocks. The function returns an incorrect result (panic instead of `true`) for a provably valid input.

---

### Likelihood Explanation

Single-transaction Bitcoin blocks (containing only the coinbase) are a normal, recurring on-chain event — they appear during low-fee periods, in early chain history, and on altcoin chains supported by this contract (Dogecoin, Litecoin, Zcash). Any relayer or consumer that submits a proof for such a block will trigger the bug deterministically. No special attacker capability is required; the caller only needs to supply a valid proof for a real block.

---

### Recommendation

Mirror the M-04 fix pattern: add the missing conjunct so the guard only fires when the proof is empty **and** the transaction hash does not equal the block's Merkle root (i.e., the block has more than one transaction):

```rust
- require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
+ require!(
+     !args.merkle_proof.is_empty() || args.tx_id == header.block_header.merkle_root,
+     "Merkle proof is empty"
+ );
```

This preserves the guard's intent (reject proofs that are empty without justification) while allowing the legitimate single-transaction case to proceed to the Merkle root comparison.

---

### Proof of Concept

1. A Bitcoin block is mined containing only the coinbase transaction `T`. Its `merkle_root == hash(T)`.
2. The relayer submits this block header via `submit_blocks`; it is accepted and stored.
3. A NEAR caller invokes `verify_transaction_inclusion` with:
   - `tx_id = hash(T)`
   - `tx_block_blockhash = <block hash>`
   - `tx_index = 0`
   - `merkle_proof = []` (empty — correct for a single-leaf tree)
   - `confirmations = 1`
4. Execution reaches line 315: `require!(!args.merkle_proof.is_empty(), ...)` → panics with `"Merkle proof is empty"`.
5. The call reverts. The mathematically valid proof is rejected.

Had the guard not fired, `compute_root_from_merkle_proof(hash(T), 0, &[])` would return `hash(T)`, which equals `header.block_header.merkle_root`, and the function would correctly return `true`. [5](#0-4) [6](#0-5)

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
