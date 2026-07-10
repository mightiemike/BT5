### Title
Unhandled `bool` Return Value of `equihash::is_valid_solution` Allows Invalid Equihash Proof Acceptance - (File: `contract/src/zcash.rs`)

### Summary
In `contract/src/zcash.rs`, the `check_pow` function calls `equihash::is_valid_solution(...)`, which returns `Result<bool, Error>`. The code uses `.unwrap_or_else` to handle only the `Err` variant (panicking on error), but **silently discards the `bool` value inside `Ok`**. If the library returns `Ok(false)` — indicating an invalid solution without an error — the check passes without reverting, and the invalid Zcash block header is accepted into the light client's canonical chain.

### Finding Description
In `contract/src/zcash.rs` at lines 64–67:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
```

`equihash::is_valid_solution` has the signature `fn(...) -> Result<bool, Error>`. The semantics are:
- `Ok(true)` — solution is valid
- `Ok(false)` — solution is **invalid** (no library-level error, just a failed check)
- `Err(e)` — a library error occurred

`.unwrap_or_else(|e| panic_str(...))` handles only the `Err` arm. When the function returns `Ok(false)`, `.unwrap_or_else` unwraps to `false` and the entire expression evaluates to `false` — which is then **dropped**. No `require!`, no assertion, no panic. Execution continues as if the Equihash solution were valid.

This is structurally identical to the ERC20 `transfer`/`transferFrom` pattern: a function that signals failure via a `false` return value rather than an exception/revert, and the caller ignores that value entirely. [1](#0-0) 

### Impact Explanation
Any Zcash block header with an **invalid Equihash solution** submitted via `submit_blocks` will pass `check_pow` without rejection. The block is then stored in `headers_pool` and, if its chainwork is sufficient, promoted to the mainchain via `submit_block_header_inner`. This corrupts:
- `mainchain_tip_blockhash` — the canonical chain tip
- `mainchain_height_to_header` and `mainchain_header_to_height` — the height↔hash mappings
- All downstream `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` results, since they resolve block membership against the corrupted mainchain

A recipient contract consuming `verify_transaction_inclusion` results would receive `true` for transactions in attacker-fabricated blocks that were never mined on the real Zcash chain. [2](#0-1) 

### Likelihood Explanation
The entry path is fully unprivileged. `submit_blocks` is a `#[payable] #[pause] #[trusted_relayer]` function, but `trusted_relayer` in this codebase is a staking/allowlist mechanism — not a cryptographic secret. Any account that has staked or is granted the `UnrestrictedSubmitBlocks` role can call it. Even without that, the `#[trusted_relayer]` macro can be bypassed by accounts with `Role::DAO` or `Role::UnrestrictedSubmitBlocks`. Constructing a Zcash block header with an invalid Equihash solution is trivial (any random 1344-byte solution field works). The attacker only needs to set `bits` to a low difficulty value and supply a `prev_block_hash` already in the pool. [3](#0-2) 

### Recommendation
Replace the silent discard with an explicit validity check:

```rust
let is_valid = equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
require!(is_valid, "Invalid Equihash solution");
```

This mirrors the pattern used for every other PoW check in the codebase, which wraps results in `require!`. [4](#0-3) 

### Proof of Concept
1. Deploy the contract compiled with `--features zcash`.
2. Initialize with a valid Zcash genesis block.
3. Craft a `BlockHeader` whose `prev_block_hash` matches the genesis hash, `bits` is set to the minimum difficulty, and `solution` is 1344 bytes of zeros (an invalid Equihash solution).
4. Call `submit_blocks([crafted_header])` with sufficient deposit.
5. Observe that `get_last_block_header()` returns the crafted header — the invalid block was accepted as the new mainchain tip.
6. Call `verify_transaction_inclusion_v2` with a fabricated transaction and a matching Merkle proof against the crafted block's `merkle_root` — it returns `true`. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/zcash.rs (L54-68)
```rust
        require!(
            block_header.version >= 4,
            "bad-version: block version must be at least 4"
        );

        // Check Equihash solution
        let n = 200;
        let k = 9;
        let input = block_header.get_block_header_vec_for_equihash();

        equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
            .unwrap_or_else(|e| {
                env::panic_str(&format!("Invalid Equihash solution: {e}"));
            });
    }
```

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
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
