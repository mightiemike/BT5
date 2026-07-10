### Title
Caller-Controlled Zero-Confirmation Bypass Allows Pre-Finality Transaction Verification — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` accept a caller-supplied `confirmations` field with no minimum floor. Passing `confirmations: 0` satisfies the confirmation check for any block in the main chain, including the chain tip. A consuming contract that calls either function with `confirmations: 0` receives `true` for a transaction that has not achieved any depth and can be erased by a reorg, directly mirroring the Fortuna class of premature-finality acceptance.

---

### Finding Description

Both verification entry points delegate to the same confirmation check in `verify_transaction_inclusion`:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [1](#0-0) 

The `confirmations` field is a plain `u64` in `ProofArgs` / `ProofArgsV2` with no lower-bound validation anywhere in the contract: [2](#0-1) 

When `confirmations = 0`, the expression `depth + 1 >= 0` is always `true` for any non-negative `u64`, so the guard is trivially bypassed. The function then proceeds to verify the Merkle proof and returns `true` for a transaction whose containing block sits at the current chain tip — a block with zero depth that is fully eligible for reorg.

`verify_transaction_inclusion_v2` delegates directly to `verify_transaction_inclusion` after its coinbase-proof check, so it inherits the same flaw: [3](#0-2) 

The official test suite explicitly exercises `confirmations: 0` as the happy-path value, confirming this is reachable and accepted: [4](#0-3) 

---

### Impact Explanation

Any NEAR contract that consumes `verify_transaction_inclusion_v2` and passes `confirmations: 0` (or any value below a safe threshold) will accept a transaction inclusion proof for a block that has not achieved finality. If a reorg subsequently removes that block from the canonical chain, the consuming contract has already acted on a transaction that no longer exists — releasing bridged funds, minting tokens, or settling a cross-chain payment against a Bitcoin transaction that was erased. This is the direct on-chain analog of the Fortuna seed-before-finality bug: the contract certifies a fact about the Bitcoin chain before that fact is irreversible.

---

### Likelihood Explanation

Bitcoin reorgs of 1–2 blocks occur in normal operation. An attacker with meaningful hash-rate can deliberately engineer a reorg of several blocks. Because `confirmations: 0` is the value used in the contract's own test suite and is never rejected by the API, consuming contracts are likely to use low or zero confirmation counts, especially during integration or when optimizing for latency. The entry path requires no privileged role: any unprivileged NEAR account can call `verify_transaction_inclusion_v2`.

---

### Recommendation

Enforce a protocol-level minimum confirmation floor inside `verify_transaction_inclusion`. Add a constant (e.g., `MIN_CONFIRMATIONS`) and reject any `ProofArgs` whose `confirmations` field falls below it:

```rust
const MIN_CONFIRMATIONS: u64 = 6; // or a chain-specific value

require!(
    args.confirmations >= MIN_CONFIRMATIONS,
    "Confirmations below minimum safe threshold"
);
```

This mirrors the Fortuna recommendation of validating against the finalized block rather than trusting a caller-supplied depth. For chains with different reorg risk profiles (Dogecoin, Zcash, Litecoin), the minimum should be set per-chain via the existing feature-flag architecture.

---

### Proof of Concept

1. Deploy the contract with any supported chain feature flag.
2. Submit a single block header via `submit_blocks` — the chain now has one block at height `N` (the tip).
3. Call `verify_transaction_inclusion_v2` with a valid Merkle proof for a transaction in that block and `confirmations: 0`.
4. The contract returns `true`. The block is at the chain tip with zero depth; it is fully reorg-eligible.
5. Submit a competing chain of headers with greater cumulative work that does not include the original block. The contract executes a reorg via `reorg_chain`, replacing the tip.
6. The consuming contract that acted on step 4's `true` result has now processed a transaction that no longer exists in the canonical Bitcoin chain tracked by the light client. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L288-308)
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
```

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```

**File:** btc-types/src/contract_args.rs (L16-36)
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

**File:** contract/tests/test_basics.rs (L796-804)
```rust
            .args_borsh(ProofArgsV2 {
                tx_id: tx_hash.clone(),
                tx_block_blockhash: block.block_hash(),
                tx_index: 1,
                merkle_proof: vec![coinbase_hash.clone()],
                coinbase_tx_id: coinbase_hash,
                coinbase_merkle_proof: vec![tx_hash],
                confirmations: 0,
            })
```
