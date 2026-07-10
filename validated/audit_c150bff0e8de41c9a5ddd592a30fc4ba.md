### Title
Missing Minimum Confirmations Bound Allows Zero-Confirmation Transaction Verification - (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations: u64` field with no enforced lower bound. Passing `confirmations = 0` trivially satisfies the confirmation check, allowing any unprivileged NEAR caller to obtain a `true` verification result for a transaction with zero on-chain confirmations.

---

### Finding Description

In `contract/src/lib.rs`, the confirmation guard is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [1](#0-0) 

Because `confirmations` is typed `u64` and the left-hand side is also `u64`, when `args.confirmations = 0` the condition reduces to `(any u64 value) >= 0`, which is unconditionally true. The only upper-bound guard present is:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [2](#0-1) 

There is no corresponding lower-bound check (`args.confirmations >= MIN_CONFIRMATIONS`). The `confirmations` field is fully caller-controlled, defined in `ProofArgs` and `ProofArgsV2`:

```rust
pub struct ProofArgs {
    ...
    pub confirmations: u64,
}
pub struct ProofArgsV2 {
    ...
    pub confirmations: u64,
}
``` [3](#0-2) 

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` via `args.into()`, so both entry points share the same broken invariant: [4](#0-3) 

---

### Impact Explanation

Any downstream protocol (bridge, DEX, escrow) that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` and trusts the returned `bool` can be deceived into treating a zero-confirmation transaction as finalized. An attacker who controls a relayer (or simply submits a single block header via `submit_blocks`) can immediately call the verification function with `confirmations = 0` and receive `true` for a transaction that has no security margin against reorganization. This corrupts the canonical proof result the contract is designed to produce.

---

### Likelihood Explanation

High. The entry path requires no privileged role. `verify_transaction_inclusion_v2` is a public, unpaused function callable by any NEAR account. The attacker only needs to submit one valid block header (paying storage deposit) and then call the verifier with `confirmations = 0`. No key leakage, social engineering, or external dependency is required.

---

### Recommendation

Enforce a protocol-defined minimum confirmation count. Add a check immediately after the existing upper-bound guard in `verify_transaction_inclusion`:

```rust
const MIN_CONFIRMATIONS: u64 = 6; // or a configurable contract parameter

require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    format!("Confirmations must be at least {MIN_CONFIRMATIONS}")
);
```

Alternatively, expose `min_confirmations` as a contract-level configuration field set at `init` time, mirroring how `gc_threshold` is already stored, so the operator can tune it per chain (Bitcoin vs. Litecoin vs. Dogecoin vs. Zcash have different reorganization risk profiles).

---

### Proof of Concept

1. Relayer (or any NEAR account) calls `submit_blocks` with a single valid block header `B` containing transaction `T`. The block is accepted and stored; `mainchain_tip_blockhash` is updated to `B`.
2. Attacker immediately calls `verify_transaction_inclusion_v2` with:
   - `tx_id` = hash of `T`
   - `tx_block_blockhash` = hash of `B`
   - `tx_index` = correct index
   - `merkle_proof` = valid Merkle path
   - `coinbase_tx_id` / `coinbase_merkle_proof` = valid coinbase proof
   - **`confirmations = 0`**
3. Inside `verify_transaction_inclusion`:
   - Upper-bound check: `0 <= gc_threshold` → passes.
   - Confirmation check: `(tip_height - tip_height) + 1 = 1 >= 0` → passes.
   - Merkle root check: passes (proof is valid).
4. Function returns `true` for a transaction with zero confirmations.
5. Any bridge or application consuming this result releases funds or executes a state transition based on an unconfirmed transaction. [5](#0-4)

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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** btc-types/src/contract_args.rs (L18-36)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
