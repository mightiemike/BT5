### Title
Caller-Supplied `confirmations = 0` Bypasses SPV Confirmation Security Guarantee — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-controlled `confirmations` parameter with **no minimum bound enforced on-chain**. Any unprivileged NEAR caller may pass `confirmations = 0`, causing both functions to return `true` for any transaction present in any mainchain block — including a block submitted seconds ago with zero Bitcoin-network confirmations. Downstream NEAR contracts that gate fund releases on a `true` result from these APIs are directly exploitable.

---

### Finding Description

`verify_transaction_inclusion` performs two confirmation-related checks:

```rust
// contract/src/lib.rs  lines 289-308
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
// ...
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [1](#0-0) 

The first check is an **upper** bound only (`confirmations <= gc_threshold`). The second check, with `confirmations = 0`, reduces to `(any non-negative value) >= 0`, which is always `true`. There is no `require!(args.confirmations >= MINIMUM, ...)` guard anywhere in the function or in `verify_transaction_inclusion_v2`, which delegates to the same path:

```rust
// contract/src/lib.rs  lines 367-368
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [2](#0-1) 

Both functions are `#[pause]`-gated but otherwise **fully public** — no role, no staking, no trusted-relayer restriction applies to callers of the verification API.

The `confirmations` field is a plain `u64` in `ProofArgs` / `ProofArgsV2` with no validation annotation:

```rust
// btc-types/src/contract_args.rs  lines 16-24
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
``` [3](#0-2) 

---

### Impact Explanation

The SPV model's entire security guarantee against double-spends rests on confirmation depth. With `confirmations = 0` accepted:

- An attacker submits a Bitcoin transaction, waits for it to appear in one block, submits that block to the NEAR contract via `submit_blocks`, then immediately calls `verify_transaction_inclusion` with `confirmations = 0`.
- The call returns `true`.
- Any downstream NEAR contract that gates an action (e.g., releasing bridged funds, minting tokens, unlocking collateral) on a `true` result from this API will execute that action for a 0-confirmation transaction — one that can still be double-spent on the Bitcoin network.

This is the direct analog to the original report: just as `totalLocked → 0` let an attacker become the observed voting majority and drain or freeze a guild, `confirmations → 0` lets an attacker become the observed "confirmed" payer and drain a downstream bridge or escrow contract.

---

### Likelihood Explanation

- **No privilege required**: the verification functions are public.
- **No special hardware or cryptographic capability required**: the attacker only needs to submit a valid block header (which the relayer already does, or the attacker can do directly via `submit_blocks` if they are a trusted relayer, or the block is already on-chain).
- **Realistic attack surface**: any NEAR contract that integrates this light client as a payment oracle is exposed. The attacker controls the `confirmations` argument entirely.

---

### Recommendation

Enforce a configurable minimum confirmation count stored in contract state, validated at both initialization and at call time:

```rust
// In InitArgs / BtcLightClient state
pub min_confirmations: u64,   // e.g. default 6 for Bitcoin

// At the top of verify_transaction_inclusion
require!(
    args.confirmations >= self.min_confirmations,
    format!("Confirmations must be at least {}", self.min_confirmations)
);
```

This mirrors the fix applied in the original report (`minimumTokensLockedForProposalCreation`): a configurable lower-bound threshold that prevents the security parameter from being driven to zero by an unprivileged caller.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature) with any valid genesis and `gc_threshold = 1000`.
2. Submit one block header via `submit_blocks` (or use the genesis block already on-chain).
3. Call `verify_transaction_inclusion` with:
   - `tx_block_blockhash` = any mainchain block hash
   - `tx_id` = any hash that satisfies the Merkle proof
   - `merkle_proof` = a valid single-element proof for that tx
   - `confirmations = 0`
4. Observe: the function returns `true` immediately, with the block having zero Bitcoin-network confirmations.
5. A downstream contract acting on this `true` result releases funds before the Bitcoin transaction is irreversible. [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L287-323)
```rust
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

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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
