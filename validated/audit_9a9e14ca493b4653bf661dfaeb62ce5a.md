### Title
Missing Minimum Confirmation Guard Allows Zero-Confirmation Transaction Verification — (`File: contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (and `verify_transaction_inclusion_v2` which delegates to it) accepts a caller-supplied `confirmations` value of `0` with no lower-bound enforcement. Any unprivileged NEAR caller can invoke the function with `confirmations = 0`, causing it to return `true` for a transaction whose block sits at the very tip of the tracked chain — a block that may be reorged away moments later. This is the direct analog of the `amountMin = 0` pattern in the reference report: a parameter that is supposed to bound/protect the outcome carries no minimum-value guard.

---

### Finding Description

`ProofArgs.confirmations` is a `u64` field supplied entirely by the caller. [1](#0-0) 

Inside `verify_transaction_inclusion` the contract enforces only an **upper** bound:

```
require!(args.confirmations <= self.gc_threshold, …);
```

and a depth check:

```
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

When `confirmations = 0`:

- `0 <= gc_threshold` — always passes.
- `(any u64 value) >= 0` — always passes (u64 arithmetic).

The function therefore returns `true` for any block that is currently in the mainchain, including the block at the very tip (height = `heaviest_block_height`), which has zero depth of burial.

`verify_transaction_inclusion_v2` inherits the same flaw because it converts its arguments and calls the deprecated function directly: [3](#0-2) 

---

### Impact Explanation

A downstream NEAR contract that consumes the boolean result of `verify_transaction_inclusion` with `confirmations = 0` will treat a zero-confirmation Bitcoin transaction as cryptographically proven. Because the BTC light client supports chain reorganisations (`reorg_chain`), a block at the tip can be displaced by a competing fork submitted in a subsequent `submit_blocks` call. [4](#0-3) 

The corrupted invariant is the **proof result**: `verify_transaction_inclusion` returns `true` for a transaction that is not yet irreversibly settled, allowing a downstream contract to release funds or update state based on a transaction that will later be invalidated by a reorg.

---

### Likelihood Explanation

The entry path requires no privilege. `verify_transaction_inclusion` is a public, non-access-controlled view function. Any NEAR account can craft a `ProofArgs` with `confirmations = 0`, point it at a block that was just submitted by the relayer, and receive `true`. The relayer continuously submits new tip blocks, so a fresh zero-depth target is always available. No key leakage, social engineering, or privileged role is needed.

---

### Recommendation

Add an explicit lower-bound guard at the top of `verify_transaction_inclusion`:

```rust
require!(args.confirmations >= MIN_CONFIRMATIONS, "confirmations too low");
```

where `MIN_CONFIRMATIONS` is a protocol constant (e.g., `1` as an absolute floor, or a chain-specific value such as `6` for Bitcoin mainnet). This mirrors the fix applied in the reference report — adding a minimum-return check so that the protective parameter cannot be zeroed out by the caller.

---

### Proof of Concept

1. Relayer submits a batch of headers; the new tip is at height `H`.
2. Attacker calls `verify_transaction_inclusion` with:
   - `tx_block_blockhash` = hash of the block at height `H` (just accepted as mainchain tip).
   - `confirmations = 0`.
3. The two `require!` guards both pass:
   - `0 <= gc_threshold` ✓
   - `(H - H) + 1 = 1 >= 0` ✓
4. `compute_root_from_merkle_proof` is evaluated; if the Merkle proof is valid, the function returns `true`.
5. A downstream contract acting on this result releases funds.
6. A competing relayer (or the same attacker) submits a heavier fork that displaces block `H`; `reorg_chain` promotes the fork, removing block `H` from the mainchain.
7. The transaction is no longer part of the canonical chain, but the downstream contract has already acted on the `true` result.

The root cause — `confirmations` has no lower-bound check — is located at: [2](#0-1)

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
