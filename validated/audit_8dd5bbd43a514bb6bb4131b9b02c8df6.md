### Title
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` Always Revert for Single-Transaction Blocks — (`contract/src/lib.rs`)

---

### Summary

Both SPV verification entry points unconditionally reject empty merkle proofs via `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")`. For a block containing exactly one transaction (the coinbase), the correct and complete merkle proof **is** the empty vector — the merkle root equals the single transaction hash, so no sibling hashes are needed. The underlying `compute_root_from_merkle_proof` handles this correctly, but the guard fires first and panics, making both functions permanently unusable for this valid input class.

---

### Finding Description

`verify_transaction_inclusion` performs the following sequence:

1. Checks `args.confirmations <= self.gc_threshold`
2. Looks up the block in `mainchain_header_to_height`
3. Checks confirmation depth
4. Fetches the header from `headers_pool`
5. **`require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")`** ← always panics for single-tx blocks
6. Calls `compute_root_from_merkle_proof` and compares to `merkle_root` [1](#0-0) 

For a block with exactly one transaction:
- The merkle root **is** the transaction hash (`merkle_root = tx_id`)
- The correct proof is `merkle_proof = []`
- `compute_root_from_merkle_proof(tx_id, 0, &[])` iterates zero times and returns `tx_id`, which equals `merkle_root` — the proof is mathematically valid [2](#0-1) 

But the guard at line 315 fires before the computation, causing an unconditional panic.

`verify_transaction_inclusion_v2` inherits the same defect because it delegates to `verify_transaction_inclusion` via `args.into()`: [3](#0-2) 

For a single-transaction block, `verify_transaction_inclusion_v2` passes all its own checks (length equality `0 == 0`, coinbase proof `coinbase_tx_id == merkle_root`) and then calls the deprecated function, which panics.

The `From<ProofArgsV2> for ProofArgs` conversion passes `merkle_proof` unchanged, so the empty vector reaches the guard intact: [4](#0-3) 

---

### Impact Explanation

Any NEAR caller invoking either SPV verification function against a block that contains only one transaction receives an unconditional panic. The function can never return `true` or `false` for this input — it always aborts. Downstream contracts that rely on SPV proofs for single-transaction blocks (e.g., early Bitcoin blocks, coinbase-only blocks in low-activity periods) cannot obtain a verification result from either the current or the deprecated API.

---

### Likelihood Explanation

Single-transaction blocks are valid on every supported chain. Bitcoin blocks 1 through roughly 170 contained only the coinbase transaction; Dogecoin and Litecoin have similar histories. Any relayer or consumer contract that submits headers for these blocks and then attempts SPV verification will always fail. The trigger requires no privilege, no special timing, and no adversarial chain data — a standard `verify_transaction_inclusion_v2` call with a legitimately empty proof suffices.

---

### Recommendation

Remove the `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard. The `compute_root_from_merkle_proof` function already handles the empty-proof case correctly (it returns the transaction hash unchanged), so the guard provides no safety and only breaks valid inputs. If a non-empty proof is desired as a policy constraint, the check should be moved to `verify_transaction_inclusion_v2` and conditioned on the block having more than one transaction, which requires reading the actual transaction count — analogous to the M-27 fix of replacing `balanceOf` with `getAccountLiquidity`.

```diff
-        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
-
         // compute merkle tree root and check if it matches block's original merkle tree root
         merkle_tools::compute_root_from_merkle_proof(
```

---

### Proof of Concept

1. Deploy the contract and submit headers including a block whose `merkle_root` equals a single transaction hash (e.g., any Bitcoin block height ≤ 170).
2. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = <coinbase_tx_hash>`
   - `tx_block_blockhash = <that block's hash>`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = <coinbase_tx_hash>`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
3. Execution path:
   - Length check: `0 == 0` ✓
   - Coinbase proof: `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` → `coinbase_tx_id == merkle_root` ✓
   - Delegates to `verify_transaction_inclusion`
   - `require!(!args.merkle_proof.is_empty(), ...)` → **panics with "Merkle proof is empty"**
4. Expected result: `true` (the transaction is provably included). Actual result: unconditional panic. [5](#0-4)

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

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
