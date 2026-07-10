### Title
Overly Restrictive Empty Merkle Proof Guard Permanently Blocks SPV Verification for Single-Transaction Blocks — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` unconditionally reject any proof whose `merkle_proof` field is empty. For a block that contains exactly one transaction (the coinbase), the correct merkle proof **is** the empty list — the merkle root equals the coinbase txid directly. The guard therefore permanently prevents any caller from verifying a legitimate transaction in a single-transaction block, breaking the core SPV invariant of the light client.

---

### Finding Description

In `contract/src/lib.rs`, `verify_transaction_inclusion` contains:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This guard fires before the actual root computation. For a block with a single transaction, the merkle tree has depth 0: the merkle root **is** the transaction hash, so the correct proof path is the empty vector. The `compute_root_from_merkle_proof` function handles this correctly — with an empty proof it simply returns the input hash unchanged:

```rust
for proof_hash in merkle_proof {   // loop body never executes
    ...
}
current_hash   // == transaction_hash
``` [2](#0-1) 

So the math is sound, but the guard fires first and panics.

The same defect propagates through `verify_transaction_inclusion_v2`, which calls the deprecated function internally after its own coinbase-proof check:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The v2 entry-point's own length-equality guard (`merkle_proof.len() == coinbase_merkle_proof.len()`) passes when both are empty (0 == 0), and the coinbase-proof check also passes because `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id`, which equals `merkle_root` in a single-tx block. The call then reaches the fatal `require!` inside the deprecated function. [4](#0-3) 

---

### Impact Explanation

The `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` API is the primary output of the light client — it is the function that downstream dApps call to confirm Bitcoin-side events before releasing funds or minting tokens on NEAR. Any dApp that needs to verify a transaction in a single-transaction block (e.g., a coinbase payout, an early-chain block, or a low-activity period block) will receive a permanent panic. The broken invariant is: *every transaction in a valid, confirmed block must be verifiable via SPV proof*. This is the direct analog of H-6's broken invariant: *every reward token must be reinvestable*.

---

### Likelihood Explanation

Single-transaction blocks are real and recurring on Bitcoin mainnet (early chain, periods of very low mempool activity) and are common on testnets and alt-chains (Dogecoin, Litecoin, Zcash) supported by this contract. Any relayer or dApp that submits such a block and then attempts SPV verification will trigger the panic on every call, with no workaround available at the contract level.

---

### Recommendation

Remove or relax the empty-proof guard. The correct fix is to allow an empty `merkle_proof` and let `compute_root_from_merkle_proof` handle it — if the result equals `header.block_header.merkle_root`, the proof is valid. Optionally add an explicit fast-path:

```rust
// Single-transaction block: merkle root == tx hash, proof is empty
if args.merkle_proof.is_empty() {
    return args.tx_id == header.block_header.merkle_root;
}
```

The same fix must be applied consistently in both `verify_transaction_inclusion` and any path that feeds into it from `verify_transaction_inclusion_v2`.

---

### Proof of Concept

1. Submit a block header whose `merkle_root` equals a known coinbase txid `T` (single-tx block). The block is accepted normally by `submit_blocks`.
2. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = T`
   - `tx_block_blockhash = <hash of the block above>`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = T`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
3. The length check passes (`0 == 0`). The coinbase-proof check passes (`compute_root_from_merkle_proof(T, 0, &[]) == T == merkle_root`). Execution enters `verify_transaction_inclusion`.
4. The contract panics: `"Merkle proof is empty"` — despite the proof being mathematically correct for a single-transaction block. [5](#0-4) [6](#0-5)

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
