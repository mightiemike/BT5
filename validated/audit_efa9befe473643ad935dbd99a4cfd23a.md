### Title
Caller-Controlled `confirmations = 0` Bypasses Finality Check in `verify_transaction_inclusion` — (`contract/src/lib.rs`)

---

### Summary

The `confirmations` field in `ProofArgs` and `ProofArgsV2` is accepted from any unprivileged NEAR caller with no lower-bound validation. Passing `confirmations = 0` makes the confirmation-depth check trivially true for every block, allowing a caller to obtain a `true` proof result for a transaction that has zero confirmed blocks behind it.

---

### Finding Description

`verify_transaction_inclusion` enforces only an **upper** bound on `confirmations`:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

The actual depth check that follows is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

Both `block_height` values are `u64`. When `args.confirmations = 0`, the right-hand side of `>=` is `0`, and any `u64` expression is `>= 0` by definition. The `require!` never panics, and execution proceeds unconditionally to the Merkle proof check. The confirmation gate is completely bypassed.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` via `self.verify_transaction_inclusion(args.into())`, passing `confirmations` unchanged through the `From<ProofArgsV2> for ProofArgs` conversion:

```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            ...
            confirmations: args.confirmations,
        }
    }
}
``` [3](#0-2) 

So both public verification endpoints are affected.

The `confirmations` field is defined as a plain `u64` with no documented minimum:

```rust
pub struct ProofArgs {
    ...
    pub confirmations: u64,
}
``` [4](#0-3) 

---

### Impact Explanation

The confirmation count is the sole on-chain mechanism that enforces Bitcoin finality before a downstream NEAR contract acts on a verified transaction (e.g., minting wrapped BTC, releasing collateral, or crediting a bridge deposit). With `confirmations = 0`, the function returns `true` for a transaction whose block was submitted one second ago and could still be reorganized away. A malicious caller can:

1. Submit a fraudulent Bitcoin transaction into a block that is accepted by the light client.
2. Immediately call `verify_transaction_inclusion_v2` with `confirmations = 0`.
3. Receive `true`, triggering any downstream NEAR contract that trusts the result.
4. Allow the Bitcoin chain to reorg the block away, leaving the NEAR side with an irreversible state change backed by a non-existent transaction.

The corrupted value is the **proof result** (`true`) returned for a transaction with zero confirmed blocks, which downstream contracts consume as a finality guarantee.

---

### Likelihood Explanation

The entry path requires no privilege: `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are both `pub` and `#[pause]`-gated (not role-gated). Any NEAR account can call them directly. The only cost is constructing a valid Merkle proof for a transaction in a block already accepted by the contract, which is straightforward for an attacker who controls the transaction. The parameter to manipulate (`confirmations`) is a plain integer field in a Borsh-serialized struct — trivial to set to `0`.

---

### Recommendation

Add a lower-bound `require!` immediately alongside the existing upper-bound check in `verify_transaction_inclusion`:

```rust
require!(
    args.confirmations >= 1,
    "Confirmations must be at least 1"
);
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

Alternatively, store a protocol-level `min_confirmations` value in contract state (set at `init` time) and enforce it here, mirroring the same pattern used for `gc_threshold`.

---

### Proof of Concept

```rust
// Attacker submits a block containing their transaction (already accepted by the contract).
// Then immediately calls:
let args = ProofArgs {
    tx_id: attacker_tx_hash,
    tx_block_blockhash: just_submitted_block_hash,
    tx_index: 0,
    merkle_proof: valid_merkle_path,
    confirmations: 0,   // <-- no lower-bound check; bypasses the depth gate
};
let result = contract.verify_transaction_inclusion(args);
// result == true, even though the block has 0 confirmations behind it.
// The downstream NEAR contract releases funds; the Bitcoin block is later reorged away.
```

The confirmation check `(tip_height - target_height + 1) >= 0` is always `true` for `u64`, so `verify_transaction_inclusion` returns whatever the Merkle proof check returns — `true` for a legitimately included transaction — with no finality enforcement whatsoever. [5](#0-4)

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
