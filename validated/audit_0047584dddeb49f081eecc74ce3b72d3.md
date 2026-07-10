### Title
Unconditional empty-proof rejection in `verify_transaction_inclusion` prevents valid single-transaction block verification - (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` unconditionally panics on empty merkle proofs via `require!(!args.merkle_proof.is_empty())`. For a Bitcoin block containing only one transaction (the coinbase), an empty merkle proof is mathematically correct — the merkle root equals the transaction hash directly. This causes `verify_transaction_inclusion_v2` to panic on a valid, reachable input, permanently blocking verification for single-transaction blocks.

### Finding Description
In `verify_transaction_inclusion` at line 315, the guard:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

is applied unconditionally before calling `compute_root_from_merkle_proof`. However, `compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` correctly handles an empty proof by returning the transaction hash itself — which is the exact correct behavior for a single-transaction block where `merkle_root == tx_id`. [1](#0-0) 

The execution path through `verify_transaction_inclusion_v2` for a single-transaction block is:

1. **Length check passes**: `merkle_proof.len() == coinbase_merkle_proof.len()` → `0 == 0` ✓
2. **Coinbase proof check passes**: `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id`, which equals `merkle_root` for a single-transaction block ✓
3. **Internal call to `verify_transaction_inclusion`** with `merkle_proof = []`
4. **`require!(!args.merkle_proof.is_empty())` panics** ✗ [2](#0-1) 

The `compute_root_from_merkle_proof` function in `merkle-tools/src/lib.rs` iterates over `merkle_proof` elements; with an empty slice, the loop body never executes and `current_hash` (initialized to `transaction_hash`) is returned directly — the correct result. [3](#0-2) 

The unconditional rejection at line 315 is therefore incorrect: it fires on a valid, well-formed proof for a valid Bitcoin block type.

### Impact Explanation
Any caller of `verify_transaction_inclusion_v2` attempting to verify the coinbase transaction in a single-transaction block receives a contract panic instead of the correct `true` result. Downstream NEAR contracts that gate cross-chain operations on this verification result are permanently blocked from processing such transactions. The broken invariant is: the function must return `true` for any valid `(tx_id, tx_index, merkle_proof, block_hash)` tuple where the proof correctly reconstructs the block's merkle root.

### Likelihood Explanation
Single-transaction blocks (containing only the coinbase) are valid in Bitcoin and occur in practice during low-traffic periods. Any relayer can submit such a block via `submit_blocks` — an unprivileged, externally reachable call. Any user can then call `verify_transaction_inclusion_v2` with the coinbase transaction and empty proofs, triggering the panic deterministically.

### Recommendation
Remove the unconditional empty-proof rejection. The coinbase merkle proof check already present in `verify_transaction_inclusion_v2` prevents the trivial forgery attack (an attacker cannot claim an arbitrary `tx_id` equals the merkle root unless it actually does):

```diff
- require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

  merkle_tools::compute_root_from_merkle_proof(
      args.tx_id,
      usize::try_from(args.tx_index).unwrap(),
      &args.merkle_proof,
  ) == header.block_header.merkle_root
```

### Proof of Concept
1. Relayer submits a valid single-transaction block (coinbase only) via `submit_blocks`.
2. User calls `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_tx_id`
   - `tx_block_blockhash = block_hash`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = coinbase_tx_id`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
3. Step 1 (length check): `0 == 0` → passes.
4. Step 2 (coinbase check): `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id == merkle_root` → passes.
5. Step 3: `verify_transaction_inclusion` called internally; `require!(!args.merkle_proof.is_empty())` **panics**. [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L313-323)
```rust
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
