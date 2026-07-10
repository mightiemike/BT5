### Title
Zero `confirmations` Bypasses Transaction Finality Check — (`contract/src/lib.rs`)

### Summary

Any unprivileged NEAR caller can invoke `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` with `confirmations = 0`. Because the contract enforces only an upper bound (`confirmations <= gc_threshold`) and no lower bound, the confirmation-depth check is trivially satisfied for every block in the chain, including the chain tip. A downstream NEAR contract that relies on the returned `true` to gate an action (e.g., a bridge mint or cross-chain settlement) will process a Bitcoin transaction that has zero finality, making it vulnerable to a Bitcoin reorg.

---

### Finding Description

`verify_transaction_inclusion` enforces two guards on the `confirmations` field supplied by the caller:

```rust
// upper-bound only
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
// depth check
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [1](#0-0) 

When `confirmations = 0`, the depth expression evaluates to `<any u64> >= 0`, which is always `true` for unsigned integers. The check is therefore a no-op, and the function proceeds to verify only the Merkle proof — returning `true` for a transaction that sits in the very tip block (or any block), regardless of how many blocks have been built on top of it.

`verify_transaction_inclusion_v2` delegates to the same function after its coinbase-proof check, so it inherits the same flaw:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [2](#0-1) 

The `confirmations` field is part of the caller-supplied `ProofArgs` / `ProofArgsV2` structs and carries no on-chain minimum constraint:

```rust
pub struct ProofArgs {
    ...
    pub confirmations: u64,
}
``` [3](#0-2) 

---

### Impact Explanation

The confirmation count is the sole on-chain mechanism that ensures a Bitcoin transaction has been buried under enough proof-of-work to be considered final before a downstream NEAR contract acts on it. Setting it to 0 removes that guarantee entirely. A downstream bridge or settlement contract that calls `verify_transaction_inclusion_v2` with `confirmations = 0` will receive `true` for a transaction that is still in the tip block and can be reorganized away. This enables a double-spend: the attacker broadcasts a Bitcoin transaction, immediately triggers the NEAR-side action (mint, unlock, etc.), then mines a longer chain that excludes the original transaction.

---

### Likelihood Explanation

The entry point is a public, unpausable-by-default read function callable by any NEAR account or contract. The `confirmations` field is a plain `u64` with no SDK-level constraint. Any integrating contract that omits or zeroes the field — whether by mistake or by attacker manipulation of the calling contract's arguments — triggers the bypass. The attack requires no privileged role, no leaked key, and no social engineering.

---

### Recommendation

Enforce a protocol-level minimum on `confirmations` inside `verify_transaction_inclusion`, analogous to how Putty Finance fixed its zero-duration bug by requiring at least 15 minutes:

```rust
const MIN_CONFIRMATIONS: u64 = 1; // or a higher value such as 6
require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    format!("confirmations must be at least {MIN_CONFIRMATIONS}")
);
```

Place this check before the existing upper-bound guard so that `confirmations = 0` is rejected at the contract boundary rather than silently treated as "always confirmed."

---

### Proof of Concept

1. Attacker submits a Bitcoin transaction; the relayer includes the containing block in the light client (block height `H`).
2. Attacker calls `verify_transaction_inclusion_v2` with:
   - `tx_block_blockhash` = hash of block `H` (the chain tip)
   - `confirmations = 0`
   - valid Merkle proof and coinbase proof for the transaction
3. The coinbase check passes (valid proof). The depth check evaluates `(H - H) + 1 = 1 >= 0` → `true`. The function returns `true`.
4. The downstream NEAR contract mints/unlocks assets.
5. Attacker mines a competing Bitcoin chain that omits the original transaction; the light client reorgs. The NEAR-side action is already irreversible. [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L289-308)
```rust
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

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
