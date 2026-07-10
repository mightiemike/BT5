### Title
`verify_transaction_inclusion_v2` Always Panics for Single-Transaction Blocks — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion_v2` unconditionally delegates to the deprecated `verify_transaction_inclusion`, which contains a hard `require!(!args.merkle_proof.is_empty(), ...)` guard. For a Bitcoin block containing exactly one transaction (the coinbase), the correct merkle proof is the empty vector. The v2 function passes all its own checks for this input, then panics inside the inner call, making it impossible for any NEAR dApp to verify a coinbase transaction in a single-transaction block.

### Finding Description

`verify_transaction_inclusion_v2` performs two checks before delegating:

1. `args.merkle_proof.len() == args.coinbase_merkle_proof.len()` — passes when both are empty (0 == 0).
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, &coinbase_merkle_proof) == header.block_header.merkle_root` — when `coinbase_merkle_proof` is empty, `compute_root_from_merkle_proof` returns `coinbase_tx_id` unchanged; for a single-transaction block the merkle root **is** the coinbase tx hash, so this check also passes. [1](#0-0) 

After both checks succeed, the function calls the deprecated inner function: [2](#0-1) 

Inside `verify_transaction_inclusion`, the very first validation after the confirmation check is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [3](#0-2) 

This `require!` unconditionally panics for an empty proof, even though an empty proof is the **only correct proof** for a single-transaction block. The v2 wrapper never guards against this case before delegating.

`compute_root_from_merkle_proof` itself handles an empty proof correctly — it simply returns the input hash unchanged — so the cryptographic logic is sound; the bug is the hard rejection in the inner function that the v2 wrapper does not bypass or check for. [4](#0-3) 

### Impact Explanation

Any NEAR dApp calling `verify_transaction_inclusion_v2` to verify a coinbase transaction (or any transaction) in a single-transaction Bitcoin block will always receive a contract panic instead of `true`. The function is the primary SPV verification entrypoint for external consumers; a panic propagates as a failed cross-contract call, breaking any downstream logic that depends on a successful verification result.

**Impact: Medium** — the broken invariant is that a cryptographically valid proof causes a panic rather than returning `true`; downstream dApps cannot distinguish this from an invalid proof and cannot work around it without switching to the deprecated v1 function (which lacks the 64-byte forgery protection).

### Likelihood Explanation

**Likelihood: Low** — single-transaction blocks are uncommon on mainnet Bitcoin but do occur (e.g., empty blocks mined immediately after a previous block). A relayer or dApp that needs to verify coinbase inclusion (e.g., for coinbase maturity checks or mining-reward proofs) would hit this path.

### Recommendation

In `verify_transaction_inclusion_v2`, before delegating to `verify_transaction_inclusion`, add an early-return for the single-transaction case:

```rust
if args.merkle_proof.is_empty() {
    // Single-transaction block: tx_id must equal the merkle root directly.
    return args.tx_id == header.block_header.merkle_root;
}
```

Alternatively, remove the `require!(!args.merkle_proof.is_empty(), ...)` guard from `verify_transaction_inclusion` and handle the empty-proof case in `compute_root_from_merkle_proof` (which already handles it correctly).

### Proof of Concept

1. Deploy the contract and initialize with a Bitcoin block whose merkle root equals a single coinbase tx hash (i.e., a block with exactly one transaction).
2. Call `verify_transaction_inclusion_v2` with:
   - `tx_id` = coinbase tx hash
   - `tx_block_blockhash` = that block's hash
   - `tx_index` = 0
   - `merkle_proof` = `[]`
   - `coinbase_tx_id` = coinbase tx hash
   - `coinbase_merkle_proof` = `[]`
   - `confirmations` = 1
3. Observe: the length check passes, the coinbase root check passes (`coinbase_tx_id == merkle_root`), then the call panics with `"Merkle proof is empty"` inside `verify_transaction_inclusion`. [5](#0-4)

### Citations

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
