### Title
No Minimum Confirmation Depth Enforced in SPV Verification Allows Reorg-Vulnerable Proof Acceptance - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a fully caller-controlled `confirmations` value with no minimum enforcement. Any unprivileged NEAR caller can pass `confirmations = 0` or `confirmations = 1`, causing the contract to return `true` for a Bitcoin transaction that sits in a block still vulnerable to a chain reorganization. The contract's stated purpose is to verify that a transaction "has reached a sufficient level of confirmation," but this invariant is never enforced on the lower bound.

### Finding Description

`ProofArgs` and `ProofArgsV2` both carry a `confirmations: u64` field that is supplied entirely by the caller. [1](#0-0) 

Inside `verify_transaction_inclusion`, the contract applies exactly one bound check on this value — an **upper** bound against `gc_threshold`: [2](#0-1) 

The confirmation-depth check that follows only verifies that the chain has grown by at least `args.confirmations` blocks beyond the target block: [3](#0-2) 

Because `confirmations` is `u64`, passing `0` makes the right-hand side `0`, and `(any u64) >= 0` is always true — the check is a no-op. Passing `1` means the transaction's block only needs to be anywhere in the current mainchain (including the tip itself). There is no floor, no protocol-defined minimum, and no documentation warning that callers must supply a safe value. `verify_transaction_inclusion_v2`, the non-deprecated production entry point, delegates directly to `verify_transaction_inclusion` after its coinbase-proof check, so it inherits the same flaw: [4](#0-3) 

### Impact Explanation

**High.** The contract is the authoritative on-chain source of truth for Bitcoin SPV proofs on NEAR. Downstream NEAR contracts that call `verify_transaction_inclusion_v2` and receive `true` will treat the Bitcoin transaction as settled. If the caller passes `confirmations = 0` or `confirmations = 1`, the proof can be accepted for a block that is subsequently reorganized out of the Bitcoin canonical chain. The contract's own mainchain state will then update (via `submit_blocks` → `reorg_chain`) to reflect the new canonical chain, but the downstream contract has already acted on the stale proof — releasing funds, minting tokens, or recording a cross-chain event that is now invalid. The corrupted state is the SPV proof result (`true`) returned for a transaction whose containing block is no longer part of the Bitcoin mainchain.

### Likelihood Explanation

**High.** The attack requires no privilege: any NEAR account can call `verify_transaction_inclusion_v2` with `confirmations = 0`. Bitcoin mainnet experiences occasional reorgs of 1–2 blocks, and deeper reorgs (up to 6 blocks) have occurred historically. The standard industry recommendation is 6 confirmations for Bitcoin. With `confirmations = 0` or `confirmations = 1`, even a 1-block reorg invalidates the proof. Because the contract places no floor on the value, any integrating contract that forgets to enforce its own minimum — or that is itself called by an adversary — is exposed.

### Recommendation

Enforce a protocol-level minimum confirmation depth inside `verify_transaction_inclusion`. A constant such as `MIN_CONFIRMATIONS: u64 = 6` (or a chain-specific value stored in `NetworkConfig`) should be added and checked before the existing upper-bound check:

```rust
const MIN_CONFIRMATIONS: u64 = 6;

require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    format!("Confirmations must be at least {MIN_CONFIRMATIONS}")
);
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
```

For chains with different reorg profiles (Dogecoin, Litecoin) the minimum should be tuned per `NetworkConfig` rather than hardcoded globally.

### Proof of Concept

1. Deploy the contract with any valid genesis and `gc_threshold = 100`.
2. Submit several Bitcoin block headers so the mainchain tip is at height `H`.
3. Call `verify_transaction_inclusion_v2` with:
   - `tx_block_blockhash` = hash of the tip block (height `H`)
   - a valid Merkle proof for a transaction in that block
   - `confirmations = 0`
4. The upper-bound check passes (`0 <= 100`). The depth check evaluates `(H - H) + 1 = 1 >= 0` → passes. The function returns `true`.
5. Now submit a competing fork that has higher chainwork. `reorg_chain` runs, the tip block at height `H` is evicted from the mainchain, and the transaction is no longer in the canonical Bitcoin chain — but the downstream contract already received `true` and acted on it. [5](#0-4) [6](#0-5)

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

**File:** contract/src/lib.rs (L563-567)
```rust
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```
