### Title
Ignored `Ok(bool)` Return Value of `equihash::is_valid_solution` Allows Invalid PoW Blocks to Be Accepted — (`contract/src/zcash.rs`)

### Summary
`check_pow` in the Zcash build calls `equihash::is_valid_solution(...)`, which returns `Result<bool, Error>`. The code handles only the `Err` variant (panicking on it) via `.unwrap_or_else`, but the `Ok(bool)` value is silently discarded. If the library returns `Ok(false)` — a valid, documented outcome meaning the solution is cryptographically invalid — the contract accepts the block without any rejection, bypassing the Equihash proof-of-work check entirely.

### Finding Description

In `contract/src/zcash.rs`, the Equihash validation is performed as follows:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
``` [1](#0-0) 

The `equihash::is_valid_solution` function returns `Result<bool, _>`. The `bool` payload inside `Ok(...)` is the actual validity verdict: `true` means the solution is valid, `false` means it is not. The `.unwrap_or_else` combinator only intercepts the `Err` arm (e.g., a malformed input that causes a library-level error) and panics. When the library successfully evaluates the solution and finds it invalid — returning `Ok(false)` — the combinator returns `false` to the call site, and that value is immediately dropped. No `require!` or assertion follows. Execution continues as if the solution were valid.

This is the direct analog of the ERC20 `approve` bug: a function returns a `bool` success indicator inside a `Result`, the `Err` path is handled, but the `false` success value is never checked.

The call site is inside `check_pow`, which is called from `submit_block_header` only when `skip_pow_verification` is `false` (the production setting). [2](#0-1) 

`check_pow` is invoked from `submit_block_header` (inherited from `lib.rs`), which is called for every header in `submit_blocks`. [3](#0-2) 

### Impact Explanation

An attacker can submit a Zcash block header with an arbitrary, cryptographically invalid Equihash solution. If `equihash::is_valid_solution` returns `Ok(false)` for that solution (the normal path for an invalid-but-parseable solution), the contract accepts the header, stores it in `headers_pool`, and potentially promotes it to the mainchain tip if its claimed chainwork is high enough. This corrupts the canonical chain state: `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height` are all updated to reflect a block that never satisfied proof-of-work. Any downstream consumer calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` against that block will receive a forged inclusion proof result. [4](#0-3) 

### Likelihood Explanation

The `equihash` crate's `is_valid_solution` is documented to return `Ok(false)` for solutions that are well-formed but fail the cryptographic check. This is the common case for any invalid solution submitted by an attacker. The attacker needs only to call `submit_blocks` with a crafted Zcash header — a public, payable, unprivileged entry point (gated only by the trusted-relayer check, which can be bypassed if the relayer role is open or if the attacker is a registered relayer). The `skip_pow_verification` flag is `false` in production, so `check_pow` is always reached. [5](#0-4) 

### Recommendation

Capture the returned `bool` and assert it:

```rust
let is_valid = equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
require!(is_valid, "Invalid Equihash solution");
```

This mirrors the pattern used for every other PoW check in the contract, where the result of a comparison is asserted with `require!`.

### Proof of Concept

1. Construct a Zcash `Header` with valid `version`, `prev_block_hash`, `bits`, `time`, and `nonce`, but with a `solution` field that is 1344 bytes of zeros (well-formed length, invalid Equihash solution).
2. Call `submit_blocks` with this header on a Zcash deployment where `skip_pow_verification = false`.
3. `check_pow` is entered. `equihash::is_valid_solution` is called and returns `Ok(false)`.
4. `.unwrap_or_else` returns `false`; the value is dropped.
5. `check_pow` returns without panicking.
6. `submit_block_header_inner` stores the header in `headers_pool` and, if its chainwork exceeds the current tip, promotes it to the mainchain.
7. `get_last_block_header()` now returns the attacker-controlled header. Any SPV proof built against this block will be accepted by `verify_transaction_inclusion`. [6](#0-5)

### Citations

**File:** contract/src/zcash.rs (L21-68)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let next_work_result =
            zcash_get_next_work_required(&self.get_config(), block_header, prev_block_header, self);

        require!(
            next_work_result.expected_bits == block_header.bits,
            "bad-diffbits: incorrect proof of work"
        );

        // Check timestamp against prev
        require!(
            block_header.time > next_work_result.prev_block_median_time_past,
            "time-too-old: block time is before the median time of the previous block"
        );

        // Check future timestamp soft fork rule introduced in v2.1.1-1.
        // This retrospectively activates at block height 2 for mainnet and regtest,
        // and 6 blocks after Blossom activation for testnet.
        //
        // MAX_FUTURE_BLOCK_TIME_MTP is typically 129600 seconds (36 hours) in Zcash
        require!(
            block_header.time
                <= next_work_result.prev_block_median_time_past + MAX_FUTURE_BLOCK_TIME_MTP,
            "time-too-far-ahead-of-mtp: block timestamp is too far ahead of median-time-past"
        );

        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp is too far ahead of local time"
        );

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

**File:** contract/src/lib.rs (L169-198)
```rust
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

**File:** contract/src/lib.rs (L650-656)
```rust
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
    }
```
