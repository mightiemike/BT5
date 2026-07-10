### Title
`env::block_timestamp_ms()` Used as Current-Time Reference for Future-Timestamp Validation Allows NEAR Validator to Bypass the 2-Hour Window Check — (`contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`, `contract/src/zcash.rs`)

---

### Summary

All four chain-specific `check_pow` implementations use `env::block_timestamp_ms()` — the timestamp of the NEAR block in which the transaction is *included* — as the "current time" reference when enforcing the `MAX_FUTURE_BLOCK_TIME_LOCAL` (2-hour) upper bound on submitted Bitcoin-family block header timestamps. Because `env::block_timestamp_ms()` is determined at inclusion time, not at submission time, a NEAR block producer can delay a `submit_blocks` transaction until the NEAR block timestamp has advanced enough to allow a Bitcoin block header whose timestamp exceeded the 2-hour window at submission time to pass the check.

---

### Finding Description

In every chain variant, `check_pow` contains the following pattern:

**Bitcoin** (`contract/src/bitcoin.rs`, lines 35–39):
```rust
let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
require!(
    block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
    "time-too-new: block timestamp too far in the future"
);
```

The identical pattern appears in `contract/src/litecoin.rs` (lines 36–40), `contract/src/dogecoin.rs` (lines 42–46), and `contract/src/zcash.rs` (lines 48–52).

`MAX_FUTURE_BLOCK_TIME_LOCAL` is defined as `2 * 60 * 60 = 7200` seconds in `btc-types/src/network.rs` (line 17).

`env::block_timestamp_ms()` is the timestamp of the NEAR block in which the `submit_blocks` call is executed. This value is not fixed at the moment the relayer broadcasts the transaction — it is set by whichever NEAR block producer includes the transaction. This is the direct analog of using `block.timestamp` as a deadline in Solidity: the effective "current time" is whatever the block producer decides it to be.

A NEAR block producer can hold the `submit_blocks` transaction in the pending pool and include it in a later block. If a Bitcoin block header has timestamp `T` such that `T > submission_near_time + 7200` (i.e., it would be rejected immediately), the producer can delay inclusion until `env::block_timestamp_ms() / 1000 >= T - 7200`, at which point the check passes and the header is accepted.

---

### Impact Explanation

The corrupted state is the **header acceptance decision** in `submit_block_header` → `submit_block_header_inner`. A Bitcoin block header that should have been rejected by the `time-too-new` guard is instead stored in `headers_pool` and, if it carries sufficient chainwork, promoted to the mainchain via `store_block_header` / `reorg_chain`. Once accepted:

- The header participates in **difficulty adjustment calculations** (its `time` field is used in `calculate_next_work_required` across all chain variants), potentially skewing the computed target.
- The header becomes a valid anchor for **SPV proof verification** via `verify_transaction_inclusion` / `verify_transaction_inclusion_v2`, meaning downstream contracts consuming the light client could receive `true` for proofs anchored to a block the Bitcoin network would have rejected.
- The corrupted `mainchain_tip_blockhash` and `mainchain_height_to_header` mappings persist in contract storage.

---

### Likelihood Explanation

The attack requires a NEAR block producer to selectively delay a specific `submit_blocks` transaction. NEAR validators are staked entities with economic incentives to behave honestly, which raises the bar. Additionally, `submit_blocks` is gated by the `#[trusted_relayer]` macro (with bypass roles `Role::DAO` and `Role::UnrestrictedSubmitBlocks`), so only approved relayers can submit headers — but the NEAR validator delaying the transaction is a separate entity from the relayer; the relayer submits a legitimate transaction and the validator delays it. The exploitable window is narrow: the Bitcoin block's timestamp must be slightly more than 2 hours ahead of the NEAR block time at submission, yet still carry valid PoW. This combination is uncommon in practice but is not impossible, particularly during periods of NEAR block time drift or when a relayer is tracking a chain with loosely-enforced timestamp rules.

---

### Recommendation

Replace `env::block_timestamp_ms()` with a reference that cannot be manipulated by delaying transaction inclusion. Concrete options:

1. **Use the MTP of the submitted headers as the upper-bound reference.** The median-time-past is already computed for the lower-bound check (`time > MTP`). Applying a fixed offset to the MTP of the chain tip for the upper-bound check removes the dependency on the NEAR block clock entirely.
2. **Require the caller to supply an explicit `max_timestamp` argument** and validate it against `env::block_timestamp_ms()` with a tolerance, so the effective deadline is committed at submission time.
3. **Document the limitation** if the NEAR block timestamp is accepted as the best available approximation, making the trust assumption explicit.

---

### Proof of Concept

1. The NEAR block timestamp at submission time is `T_near` (in seconds).
2. A trusted relayer broadcasts `submit_blocks` containing a Bitcoin block header with `time = T_near + 7201` (1 second beyond the 2-hour limit).
3. Without delay, `env::block_timestamp_ms() / 1000 = T_near`, so `T_near + 7201 > T_near + 7200` → `require!` panics → header rejected.
4. A malicious NEAR block producer withholds the transaction for 2 seconds and includes it in a block with timestamp `T_near + 2`.
5. At inclusion time, `env::block_timestamp_ms() / 1000 = T_near + 2`, so `T_near + 7201 <= (T_near + 2) + 7200 = T_near + 7202` → check passes.
6. `submit_block_header_inner` stores the header; if its chainwork exceeds the current tip, `reorg_chain` promotes it to the mainchain, corrupting `mainchain_tip_blockhash` and all downstream height/hash mappings.

Affected lines: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contract/src/zcash.rs (L48-52)
```rust
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp is too far ahead of local time"
        );
```

**File:** btc-types/src/network.rs (L17-17)
```rust
pub const MAX_FUTURE_BLOCK_TIME_LOCAL: u32 = 2 * 60 * 60;
```
