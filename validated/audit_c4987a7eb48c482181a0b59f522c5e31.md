### Title
Unchecked Boolean Return Value of `equihash::is_valid_solution` Allows Invalid PoW Block Acceptance - (File: `contract/src/zcash.rs`)

### Summary
In `contract/src/zcash.rs`, the `check_pow` function calls `equihash::is_valid_solution(...)` and only handles the `Err` variant via `.unwrap_or_else`. The `Ok(bool)` return value is silently discarded. If the library returns `Ok(false)` for an invalid solution, the block passes PoW verification and is accepted into the canonical chain.

### Finding Description
`check_pow` in the Zcash build path ends with:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
```

`equihash::is_valid_solution` returns `Result<bool, Error>`. `.unwrap_or_else(|e| ...)` handles only the `Err` branch (by panicking). When the function returns `Ok(false)` — indicating an invalid but parseable solution — `.unwrap_or_else` extracts the `false` value and the statement completes without any branch on that boolean. The `false` is discarded. Execution continues and the block is stored as valid.

This is structurally identical to the reported pattern: a sub-operation signals failure via a return value rather than an exception, and the caller treats the call as successful because it did not throw. [1](#0-0) 

### Impact Explanation
An attacker who submits a Zcash block header with a syntactically well-formed but cryptographically invalid Equihash solution bypasses the only PoW check for that block. The block is inserted into `headers_pool` and, if its `chain_work` exceeds the current tip, promoted to the canonical chain via `reorg_chain`. This corrupts:

- `mainchain_tip_blockhash` — the canonical tip pointer
- `mainchain_height_to_header` and `mainchain_header_to_height` — the height↔hash index
- Any subsequent call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` that references the fake block will return `true` for attacker-fabricated transactions [2](#0-1) [3](#0-2) 

### Likelihood Explanation
The entry point is the public, unprivileged `submit_blocks` function (gated only by the optional `trusted_relayer` macro, which can be bypassed via the `UnrestrictedSubmitBlocks` role or when the relayer stake mechanism is not enforced). An attacker needs only to craft a Zcash header whose Equihash solution is invalid but causes the `equihash` crate to return `Ok(false)` rather than `Err`. This is the documented behavior of that crate for solutions that parse correctly but fail the constraint check. No privileged access, key material, or social engineering is required. [4](#0-3) 

### Recommendation
Replace the discarded-result call with an explicit boolean check:

```rust
let valid = equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| env::panic_str(&format!("Invalid Equihash solution: {e}")));
require!(valid, "Invalid Equihash solution");
```

This mirrors the pattern used everywhere else in the PoW path (`require!` after computing a value), and ensures that `Ok(false)` is treated as a hard rejection rather than a silent pass.

### Proof of Concept

1. Attacker constructs a `ZcashHeader` with valid fields (correct `bits`, valid timestamp, version ≥ 4) but with a `solution` field that is syntactically well-formed yet cryptographically invalid (e.g., duplicate indices that satisfy the length check but not the Equihash constraint).
2. Attacker calls `submit_blocks([crafted_header])` on the NEAR contract.
3. `submit_block_header` → `check_target` → `check_pow` is reached.
4. `equihash::is_valid_solution(200, 9, &input, &nonce, &solution)` returns `Ok(false)`.
5. `.unwrap_or_else(|e| panic_str(...))` extracts `false`; the statement is a no-op; no `require!` fires.
6. `check_pow` returns normally; `submit_block_header_inner` stores the block.
7. If the attacker's fabricated `chain_work` exceeds the current tip, `reorg_chain` promotes the fake block to the canonical chain.
8. A downstream consumer calling `verify_transaction_inclusion` with a transaction hash and a Merkle proof crafted against the fake block's `merkle_root` receives `true`. [1](#0-0) [5](#0-4)

### Citations

**File:** contract/src/zcash.rs (L59-67)
```rust
        // Check Equihash solution
        let n = 200;
        let k = 9;
        let input = block_header.get_block_header_vec_for_equihash();

        equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
            .unwrap_or_else(|e| {
                env::panic_str(&format!("Invalid Equihash solution: {e}"));
            });
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

**File:** contract/src/lib.rs (L517-528)
```rust
        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }

        self.submit_block_header_inner(current_header, &prev_block_header);
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
