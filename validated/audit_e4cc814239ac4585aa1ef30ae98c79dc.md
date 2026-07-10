### Title
Zero Confirmations Allowed in Transaction Inclusion Verification — No Reorg Protection (`File: contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations` value of `0` with no minimum enforcement. This is a direct analog to the reported "0 Slippage Protection" bug: a safety-bound parameter that should be non-zero is never validated, allowing any unprivileged NEAR caller to obtain a `true` proof result for a transaction with zero confirmation depth.

### Finding Description

`ProofArgs.confirmations` is a `u64` field supplied entirely by the caller. [1](#0-0) 

Inside `verify_transaction_inclusion`, two guards exist for `confirmations`:

**Guard 1** — upper-bound only:
```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [2](#0-1) 

**Guard 2** — depth check:
```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [3](#0-2) 

Neither guard enforces a **lower bound** of `confirmations >= 1`. When `confirmations = 0`:

- Guard 1: `0 <= gc_threshold` → always passes.
- Guard 2: `(any u64 value) >= 0` → always passes (u64 arithmetic, never negative).

Execution then falls through to the Merkle proof check, which is independent of confirmation depth. If the Merkle proof is valid, the function returns `true` — certifying a transaction with **zero confirmation depth**.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase proof check, so it inherits the same flaw. [4](#0-3) 

### Impact Explanation

Downstream contracts or off-chain bridges that call `verify_transaction_inclusion[_v2]` and trust its `true` return value will accept a Bitcoin transaction as "confirmed" even when it has zero on-chain confirmations. The target block could be on a fork that is immediately reorganized away. This enables a double-spend: broadcast a transaction, obtain a `true` SPV proof with `confirmations = 0` before any reorg, and redeem funds on NEAR while the Bitcoin transaction is rolled back.

### Likelihood Explanation

The entry point is fully public and permissionless — any NEAR account can call `verify_transaction_inclusion` with `confirmations = 0`. No privileged role, leaked key, or social engineering is required. The only prerequisite is a valid Merkle proof for a transaction in any block already submitted to the light client, which is trivially obtainable for any real transaction.

### Recommendation

Add an explicit lower-bound check at the start of `verify_transaction_inclusion`:

```rust
require!(args.confirmations >= 1, "Confirmations must be at least 1");
```

Alternatively, enforce a protocol-level minimum (e.g., `>= 6` for Bitcoin mainnet) and reject calls that supply a value below it, mirroring how `amountAMin`/`amountBMin` should be non-zero to provide meaningful slippage protection.

### Proof of Concept

1. Deploy the contract (Bitcoin feature, `skip_pow_verification = false`).
2. Submit a valid block containing transaction `tx_id` with a valid Merkle proof.
3. Call:
   ```json
   verify_transaction_inclusion({
     "tx_id": "<valid_tx_id>",
     "tx_block_blockhash": "<block_hash>",
     "tx_index": 0,
     "merkle_proof": ["<valid_proof_hashes>"],
     "confirmations": 0
   })
   ```
4. The call returns `true` immediately, with the block having zero confirmations — even if the chain tip is at the same height as the target block, or the block is on a fork not yet resolved.

The root cause is the absence of `require!(args.confirmations >= 1, ...)` in `verify_transaction_inclusion` at `contract/src/lib.rs`. [5](#0-4)

### Citations

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
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
