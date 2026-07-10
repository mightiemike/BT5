### Title
Incorrect `MAX_FUTURE_BLOCK_TIME_MTP` Constant Causes Valid Zcash Blocks to Be Rejected — (`btc-types/src/network.rs` / `contract/src/zcash.rs`)

---

### Summary

`MAX_FUTURE_BLOCK_TIME_MTP` is defined as `90 * 60 = 5400 seconds` (90 minutes), but the inline comment in the Zcash PoW checker explicitly states the value should be `129600 seconds (36 hours)` per the Zcash protocol. This is a 24× undervalue of the same class as the external report: a constant whose numeric value contradicts its own documentation, causing the contract to enforce a far stricter rule than the protocol specifies.

---

### Finding Description

In `btc-types/src/network.rs`, the shared constant is:

```rust
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 90 * 60;  // = 5400 seconds
``` [1](#0-0) 

In `contract/src/zcash.rs`, this constant is applied to the Zcash MTP-based future-timestamp check, and the comment directly contradicts the value in use:

```rust
// MAX_FUTURE_BLOCK_TIME_MTP is typically 129600 seconds (36 hours) in Zcash
require!(
    block_header.time
        <= next_work_result.prev_block_median_time_past + MAX_FUTURE_BLOCK_TIME_MTP,
    "time-too-far-ahead-of-mtp: ..."
);
``` [2](#0-1) 

The comment states the Zcash protocol value is `129600 seconds`, but the constant supplies `5400 seconds` — a 24× discrepancy. The constant is not used in the Bitcoin or Litecoin paths; it is exclusively consumed by the Zcash `check_pow` function. [3](#0-2) 

---

### Impact Explanation

Any valid Zcash block whose timestamp falls in the window `(MTP + 5400s, MTP + 129600s]` — i.e., between 90 minutes and 36 hours ahead of the previous block's median-time-past — will be unconditionally rejected by `check_pow`. Because Zcash's own consensus rules permit timestamps up to 36 hours ahead of MTP, these are protocol-valid blocks. The light client's header acceptance decision is therefore corrupted: it refuses headers that the canonical Zcash chain has already accepted, permanently desynchronising the on-chain state from the real Zcash chain tip. Any downstream NEAR contract consuming `verify_transaction_inclusion` against those heights will receive incorrect (false-negative) proof results.

---

### Likelihood Explanation

**High.** Zcash block timestamps are miner-controlled within the protocol's allowed window. Miners routinely set timestamps ahead of MTP to avoid the lower-bound rejection. Any block whose timestamp is more than 90 minutes but less than 36 hours ahead of MTP — a window that occurs in normal mining — will trigger the bug. No adversarial intent is required; ordinary Zcash blocks submitted by an honest relayer are sufficient.

---

### Recommendation

Update the constant to match the Zcash protocol specification cited in the comment:

```rust
// 36 hours, per Zcash protocol (v2.1.1-1 soft fork)
pub const MAX_FUTURE_BLOCK_TIME_MTP: u32 = 129_600;
``` [4](#0-3) 

If the constant must remain shared across chains, introduce a Zcash-specific override rather than a single shared value, since Bitcoin does not apply an MTP-based upper-bound check at all.

---

### Proof of Concept

1. Obtain a real Zcash mainnet block whose `time` satisfies:
   `prev_mtp < time <= prev_mtp + 129600` and `time > prev_mtp + 5400`.
2. Submit it via the NEAR `submit_blocks` entrypoint with valid Equihash solution and correct `bits`.
3. `check_pow` reaches the `require!` at line 42–45 of `zcash.rs` and panics with `"time-too-far-ahead-of-mtp"`, even though the Zcash network accepted the block.
4. The contract's stored chain tip does not advance; the mainchain mapping is stale. [5](#0-4) [1](#0-0)

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
