### Title
Wrong `MAX_FUTURE_BLOCK_TIME_MTP` Constant Makes Zcash MTP Future-Block Check 24× Too Strict — (`btc-types/src/network.rs`)

---

### Summary

`MAX_FUTURE_BLOCK_TIME_MTP` is hardcoded to `90 * 60 = 5400 seconds` (90 minutes), but the Zcash protocol specifies the MTP-based future-block-time limit as **36 hours (129 600 seconds)**. The contract's own inline comment acknowledges the correct value. As a result, every Zcash block whose timestamp falls between 90 minutes and 36 hours ahead of the previous block's median-time-past is unconditionally rejected, even though it is fully valid on the Zcash network.

---

### Finding Description

In `btc-types/src/network.rs` two shared time-limit constants are defined:

```rust
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;       // 5 400 s  ← wrong for Zcash MTP check
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60; // 7 200 s
``` [1](#0-0) 

`MAX_FUTURE_BLOCK_TIME_MTP` is consumed exclusively in the Zcash `check_pow` path:

```rust
// MAX_FUTURE_BLOCK_TIME_MTP is typically 129600 seconds (36 hours) in Zcash
require!(
    block_header.time
        <= next_work_result.prev_block_median_time_past + MAX_FUTURE_BLOCK_TIME_MTP,
    "time-too-far-ahead-of-mtp: ..."
);
``` [2](#0-1) 

The Zcash reference implementation enforces `block.GetBlockTime() > pindexPrev->GetMedianTimePast() + 36 * 60 * 60` (129 600 s). The value `90 * 60 = 5 400 s` is the **local-clock** limit used by Zcash, not the MTP limit. The two limits have been conflated: the MTP constant carries the local-clock value, and the local-clock constant (`MAX_FUTURE_BLOCK_TIME_LOCAL = 7 200 s`) does not match Zcash's 90-minute local-clock rule either.

The bug class is identical to the reported `Goldigovernor` issue: a protocol constant is derived from the wrong time assumption, making an enforced bound 24× tighter than the specification requires.

---

### Impact Explanation

Any valid Zcash block whose `time` field satisfies

```
prev_MTP + 5400 < block.time ≤ prev_MTP + 129600
```

is rejected by `check_pow` with `"time-too-far-ahead-of-mtp"`. Because `submit_block_header` calls `check_pow` for every non-genesis block, the relayer cannot advance the on-chain tip past such a block. The light client stalls: `get_last_block_height` freezes, and all subsequent calls to `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` for blocks at or above the stall height return false or panic, breaking the SPV guarantee for downstream consumers. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation

Zcash's 75-second block target means the MTP of 11 blocks lags the chain tip by roughly 8–10 minutes. A block timestamped 91 minutes ahead of MTP (≈ 81 minutes ahead of wall clock) is unusual but not impossible: miners are permitted to set timestamps up to 36 hours ahead of MTP by protocol, and during hash-rate drops or deliberate timestamp manipulation within the allowed window, such blocks appear on mainnet. Any single such block in the Zcash canonical chain permanently stalls this light client.

---

### Recommendation

Replace the shared constant with the correct Zcash MTP value, either by splitting the constant per chain or by correcting the value:

```rust
// Zcash: MTP-based future block time limit (36 hours)
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 36 * 60 * 60; // 129 600 s

// Zcash: local-clock future block time limit (90 minutes)
pub const MAX_FUTURE_BLOCK_TIME_LOCAL_ZCASH: u32 = 90 * 60; // 5 400 s
```

Use `MAX_FUTURE_BLOCK_TIME_LOCAL_ZCASH` for the local-clock check in `zcash.rs` and `MAX_FUTURE_BLOCK_TIME_MTP = 129_600` for the MTP check.

---

### Proof of Concept

1. The Zcash canonical chain produces a block `B` at height `H` with `B.time = prev_MTP + 7200` (2 hours ahead of MTP — well within the 36-hour protocol limit).
2. The relayer calls `submit_blocks([B])`.
3. Inside `check_pow`, the contract evaluates:
   ```
   B.time <= prev_MTP + MAX_FUTURE_BLOCK_TIME_MTP
   7200   <= 5400   → false → panic "time-too-far-ahead-of-mtp"
   ```
4. The transaction reverts; block `B` is never stored.
5. All subsequent blocks build on `B`; the relayer cannot submit any of them (`PrevBlockNotFound` for every block after `B`).
6. `verify_transaction_inclusion_v2` for any transaction in block `B` or later panics with `"block does not belong to the current main chain"`, silently breaking cross-chain verification for all downstream callers. [5](#0-4) [6](#0-5)

### Citations

**File:** btc-types/src/network.rs (L5-17)
```rust
pub const MEDIAN_TIME_SPAN: usize = 11;

/**
 * Maximum amount of time that a block timestamp is allowed to be ahead of the
 * median-time-past of the previous block.
 */
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;

/**
 * Maximum amount of time that a block timestamp is allowed to be ahead of the
 * current local time.
 */
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60;
```

**File:** contract/src/zcash.rs (L20-68)
```rust
    // Reference implementation: https://github.com/zcash/zcash/blob/v6.2.0/src/main.cpp#L5019
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
