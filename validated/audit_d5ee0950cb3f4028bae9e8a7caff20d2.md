### Title
Single-Transaction Block Inclusion Proof Permanently Rejected Due to Empty Merkle Proof Guard — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` (and `verify_transaction_inclusion_v2`, which delegates to it) unconditionally panics when `merkle_proof` is empty. For a Bitcoin block containing exactly one transaction, the valid Merkle proof **is** an empty vector — the Merkle root equals the transaction hash directly. The underlying `compute_root_from_merkle_proof` already handles this case correctly, but the guard fires first, permanently blocking any caller from verifying a transaction in a single-transaction block.

### Finding Description
In `contract/src/lib.rs` line 315, after all confirmation and chain-membership checks pass, the function unconditionally rejects an empty proof:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

In Bitcoin's Merkle tree construction, a block with a single transaction has a Merkle root that is exactly the transaction's TXID. No sibling hashes are needed; the proof is an empty vector. The `merkle_proof_calculator` in `merkle-tools/src/lib.rs` confirms this: its `while current_hashes.len() > 1` loop never executes for a one-element input, so it returns an empty `Vec`. [2](#0-1) 

`compute_root_from_merkle_proof` also handles this correctly: with an empty proof its `for` loop is a no-op and it returns `transaction_hash` unchanged — which is exactly the Merkle root for a single-transaction block. [3](#0-2) 

The guard at line 315 fires before `compute_root_from_merkle_proof` is ever called, making the correct result unreachable.

`verify_transaction_inclusion_v2` does not escape this: it validates the coinbase proof independently, then calls the deprecated `verify_transaction_inclusion` via `args.into()`, hitting the same guard. [4](#0-3) 

### Impact Explanation
Any NEAR contract or unprivileged caller that submits a well-formed, mathematically valid inclusion proof for a transaction in a single-transaction Bitcoin block will receive a permanent panic. Because the check is unconditional and the proof is structurally correct, no retry or reformulation can succeed without a contract upgrade. Downstream protocols that gate asset releases, bridge withdrawals, or state transitions on `verify_transaction_inclusion_v2` returning `true` are permanently blocked for this class of blocks. Early Bitcoin blocks (heights 0–170 and many mined blocks throughout history) frequently contain only the coinbase transaction.

### Likelihood Explanation
Single-transaction blocks are a real and recurring Bitcoin phenomenon — any miner producing an empty block creates one. A relayer that faithfully submits such a block header to the contract will cause the header to be accepted and stored in the mainchain, but any subsequent call to verify the coinbase transaction in that block will always revert. The entry path requires no privilege: any NEAR account can call `verify_transaction_inclusion_v2` with a valid `ProofArgsV2` whose `merkle_proof` and `coinbase_merkle_proof` are both empty.

### Recommendation
Remove the `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard. The downstream call to `compute_root_from_merkle_proof` already handles the empty-proof case correctly and will return the transaction hash unchanged, which is the correct Merkle root for a single-transaction block. The guard provides no security value and only breaks a valid code path.

```rust
// Remove this line:
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

### Proof of Concept

1. A Bitcoin block at some height `H` contains exactly one transaction (the coinbase), with TXID `T`. Its Merkle root is `T`.
2. The relayer submits the block header; it is accepted and stored in `mainchain_height_to_header` and `headers_pool`.
3. A NEAR caller invokes `verify_transaction_inclusion_v2` with:
   - `tx_id = T`
   - `tx_block_blockhash = hash(block H)`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = T`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
4. The length check `merkle_proof.len() == coinbase_merkle_proof.len()` passes (0 == 0).
5. The coinbase proof check `compute_root_from_merkle_proof(T, 0, &[]) == merkle_root` passes (T == T).
6. `verify_transaction_inclusion` is called; `require!(!args.merkle_proof.is_empty(), ...)` panics with `"Merkle proof is empty"`.
7. The call reverts. The valid transaction can never be verified regardless of how many confirmations accumulate.

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L347-369)
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
    }
```

**File:** merkle-tools/src/lib.rs (L9-31)
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
