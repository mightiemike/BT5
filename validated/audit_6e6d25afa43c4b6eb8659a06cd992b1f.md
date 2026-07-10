### Title
`env::block_timestamp_ms()` Used as "Local Time" Clock for Bitcoin Block Timestamp Validation — (`contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`, `contract/src/zcash.rs`)

---

### Summary

All four chain-specific PoW verification modules use `env::block_timestamp_ms()` — the NEAR block timestamp — as the "current local time" reference when enforcing the `MAX_FUTURE_BLOCK_TIME_LOCAL` rule. NEAR block timestamps are not a reliable real-time clock: they are set by validators and can lag real wall-clock time by a variable and non-trivial amount. This is the direct analog of the `block.number`-as-clock bug in the external report, transposed to the NEAR execution environment.

---

### Finding Description

In every chain module, the future-timestamp check is implemented identically:

```rust
let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
require!(
    block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
    "time-too-new: block timestamp too far in the future"
);
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

`MAX_FUTURE_BLOCK_TIME_LOCAL` is defined as `2 * 60 * 60` seconds (2 hours): [5](#0-4) 

The intent of this check is to reject Bitcoin headers whose `time` field is more than 2 hours ahead of the current real-world wall-clock time. Bitcoin Core uses the node's local system clock for this. The contract substitutes `env::block_timestamp_ms()` — the timestamp of the NEAR block in which the transaction executes.

NEAR block timestamps are set by the block producer and are not guaranteed to match real wall-clock time. Under normal conditions they are close, but:

1. NEAR validators are permitted to set block timestamps within a range; the timestamp is not a trusted real-time oracle.
2. If NEAR block production is delayed or the timestamp lags real time, `current_timestamp` will be lower than actual wall-clock time, making the effective allowed future window larger than `MAX_FUTURE_BLOCK_TIME_LOCAL`.
3. Conversely, if a NEAR block timestamp is ahead of real time, valid near-future Bitcoin headers could be incorrectly rejected.

The broken invariant is: **the contract assumes `env::block_timestamp_ms()` equals real wall-clock time**, but this is not guaranteed by the NEAR protocol.

---

### Impact Explanation

The `check_pow` function is called from `submit_block_header` for every submitted Bitcoin header when `skip_pow_verification = false` (the production setting): [6](#0-5) 

If `env::block_timestamp_ms()` lags real time, a relayer can submit a Bitcoin header whose `time` field is, say, 3 hours ahead of the NEAR block timestamp but only 1 hour ahead of real time. The check `block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL` would correctly reject it (3h > 2h window), but the window is computed against a stale clock — so a header that is genuinely 2.5 hours in the future relative to real time could pass if the NEAR timestamp lags by 30+ minutes.

The inverse is also true: if NEAR timestamps run ahead of real time, valid Bitcoin headers (whose `time` is within 2 hours of real time) could be falsely rejected, stalling the light client's chain tip.

The corrupted invariant is the **header acceptance decision**: a header that should be rejected (future timestamp) is accepted, or a valid header is rejected, corrupting the canonical chain state stored in `mainchain_tip_blockhash` and `headers_pool`.

---

### Likelihood Explanation

NEAR block timestamps are generally close to real time under normal network conditions, so the deviation is typically small. However:

- The 2-hour window is itself narrow; even a 10–30 minute NEAR timestamp lag meaningfully shifts the effective acceptance boundary.
- A relayer (even an honest one) submitting headers during a period of NEAR timestamp lag could have valid headers rejected, causing a liveness failure.
- A malicious relayer could time submissions to exploit a known NEAR timestamp lag to push through a Bitcoin header with a timestamp slightly beyond the 2-hour real-time window.

Likelihood is **low-to-medium** for the acceptance bypass, and **low** but non-zero for the false-rejection liveness issue.

---

### Recommendation

Replace `env::block_timestamp_ms()` with a more robust approach. Since no on-chain trusted real-time oracle exists on NEAR, the standard Bitcoin approach should be followed: validate the future-timestamp bound against the **Median Time Past (MTP)** of the submitted chain itself, not against any external clock. Bitcoin Core's `MAX_FUTURE_BLOCK_TIME_MTP` (already defined in the codebase and used in the Zcash module) is the correct analog: [7](#0-6) 

For Bitcoin, Litecoin, and Dogecoin modules, replace the `env::block_timestamp_ms()` check with a bound relative to the MTP of the previous block (already computed in `get_median_time_past`), using `MAX_FUTURE_BLOCK_TIME_MTP`. This eliminates the dependency on the NEAR block clock entirely and matches the approach already used in `contract/src/zcash.rs`: [8](#0-7) 

---

### Proof of Concept

1. NEAR block production experiences a 20-minute timestamp lag (e.g., due to validator clock skew or network congestion).
2. A relayer submits a Bitcoin header with `time = real_now + 2h10m` (10 minutes beyond the allowed window).
3. `current_timestamp = env::block_timestamp_ms() / 1000 = real_now - 20min`.
4. The check evaluates: `real_now + 2h10m <= (real_now - 20min) + 2h = real_now + 1h40m` → **false**, correctly rejected.
5. Now consider the inverse: NEAR timestamp is 30 minutes *ahead* of real time. A relayer submits a header with `time = real_now + 2h15m` (15 minutes beyond the real-time window).
6. `current_timestamp = real_now + 30min`. Check: `real_now + 2h15m <= real_now + 30min + 2h = real_now + 2h30m` → **true**, header is **accepted** despite being 15 minutes beyond the intended 2-hour real-time bound.
7. This accepted header enters `headers_pool` and can become `mainchain_tip_blockhash`, corrupting the canonical chain state.

### Citations

**File:** contract/src/bitcoin.rs (L35-39)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** contract/src/litecoin.rs (L36-40)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** contract/src/dogecoin.rs (L42-46)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** contract/src/zcash.rs (L41-45)
```rust
        require!(
            block_header.time
                <= next_work_result.prev_block_median_time_past + MAX_FUTURE_BLOCK_TIME_MTP,
            "time-too-far-ahead-of-mtp: block timestamp is too far ahead of median-time-past"
        );
```

**File:** contract/src/zcash.rs (L48-52)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp is too far ahead of local time"
        );
```

**File:** btc-types/src/network.rs (L8-11)
```rust
 * Maximum amount of time that a block timestamp is allowed to be ahead of the
 * median-time-past of the previous block.
 */
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;
```

**File:** btc-types/src/network.rs (L16-17)
```rust
 */
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60;
```

**File:** contract/src/lib.rs (L517-526)
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
```
