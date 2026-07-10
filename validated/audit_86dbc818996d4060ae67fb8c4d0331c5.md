### Title
`confirmations = 0` Bypasses Confirmation Security Check in `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` — (`File: contract/src/lib.rs`)

---

### Summary

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-controlled `confirmations` parameter with no lower-bound validation. Passing `confirmations = 0` silently bypasses the entire confirmation-depth security check while the Merkle proof verification still executes and returns `true`. Any downstream NEAR contract that gates fund releases on this result can be exploited to accept a Bitcoin transaction that has zero on-chain confirmations and is therefore still reorganizable.

---

### Finding Description

`verify_transaction_inclusion` enforces two guards around the `confirmations` field:

```rust
// upper-bound only — no lower-bound
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

When `confirmations = 0`:

- `0 <= gc_threshold` is always true → first guard passes.
- `saturating_sub` returns at minimum `0`, so `0 + 1 = 1 >= 0` is always true → second guard passes unconditionally for **any** block on the mainchain, including the tip block itself (0 blocks built on top of it).

The function then proceeds to Merkle proof verification and returns `true` if the proof is valid:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [3](#0-2) 

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase proof check, so it inherits the same flaw:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [4](#0-3) 

The `confirmations` field is part of the caller-supplied `ProofArgs` / `ProofArgsV2` structs, fully attacker-controlled with no contract-side floor enforcement. [5](#0-4) 

---

### Impact Explanation

The contract is explicitly designed as a security primitive for downstream NEAR contracts to verify Bitcoin transaction inclusion before releasing funds or updating state. A downstream contract that passes a user-supplied `confirmations` value (a realistic pattern for flexible bridge or escrow designs) can be exploited: the attacker submits a Bitcoin transaction, immediately calls the downstream contract with `confirmations = 0` before any reorganization window closes, receives the bridged asset, and then the Bitcoin transaction is reorganized out — a classic SPV double-spend. Even a downstream contract that hardcodes `confirmations` is not affected, but the light client contract itself provides no safety net for those that do not.

**Impact: 5 / 10**

---

### Likelihood Explanation

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are public, unpermissioned, and callable by any NEAR account when the contract is not paused. No staking, role, or deposit is required. The only prerequisite is that the target block is already on the mainchain (submitted by the relayer), which is always true for the tip block. The attack path is direct and requires no privileged access.

**Likelihood: 4 / 10**

---

### Recommendation

Add a minimum confirmation floor at the entry of both verification functions:

```rust
require!(args.confirmations >= 1, "Confirmations must be at least 1");
```

Alternatively, enforce a protocol-level minimum (e.g., 6 for Bitcoin finality) and document it clearly. The check should be placed before any storage reads to fail fast.

---

### Proof of Concept

1. Relayer submits block `B` at height `H` (the current tip). `B` contains transaction `TX`.
2. Attacker immediately calls `verify_transaction_inclusion_v2` with:
   - `tx_id` = hash of `TX`
   - `tx_block_blockhash` = hash of `B`
   - `merkle_proof` = valid Merkle path for `TX` in `B`
   - `coinbase_tx_id` + `coinbase_merkle_proof` = valid coinbase proof for `B`
   - **`confirmations = 0`**
3. Guard 1: `0 <= gc_threshold` → passes.
4. Guard 2: `(H - H) + 1 = 1 >= 0` → passes.
5. Merkle proof is valid → function returns `true`.
6. Downstream contract releases funds.
7. Bitcoin network reorganizes block `B` out; `TX` is never confirmed. [6](#0-5) [7](#0-6)

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

**File:** btc-types/src/contract_args.rs (L1-1)
```rust
use near_sdk::near;
```
