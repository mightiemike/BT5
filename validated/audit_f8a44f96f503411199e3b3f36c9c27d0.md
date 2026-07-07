### Title
`nonDefaultFeeTierMask` is `uint128` but `tier` is `uint32`, silently discarding custom fee rates for tiers ≥ 128 — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.sol` uses a `uint128` bitmask (`nonDefaultFeeTierMask`) to track which fee tiers have custom rates. Because `tier` is `uint32`, the protocol intends to support up to ~4 billion distinct tiers. However, the bitmask can only represent 128 tiers (bits 0–127). Any tier ≥ 128 can have custom rates written to storage but those rates are permanently unreachable: the mask bit is never set on write, and the mask check always fails on read. Affected users are silently forced onto the default 2 bps taker / 0 maker fee schedule regardless of what was configured for their tier.

---

### Finding Description

**Write path — bit is never set for tier ≥ 128**

In `updateTierFeeRates`, the mask is updated as:

```solidity
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
``` [1](#0-0) 

`uint128(1) << txn.tier` is a `uint128` shift. In Solidity 0.8.x, shifting a value by ≥ its bit-width produces 0. So for any `txn.tier ≥ 128`, the expression evaluates to `0`, and the OR is a no-op. The custom rates are written to `feeRates[tier][productId]` but the mask bit that gates their retrieval is never set.

**Read path — check always fails for tier ≥ 128**

```solidity
if (nonDefaultFeeTierMask & (1 << tier) != 0) {
    return feeRates[tier][productId];
}
return FeeRates({
    makerRateX18: 0,
    takerRateX18: 200_000_000_000_000 // 2 bps
});
``` [2](#0-1) 

Here `1` is a `uint256` literal, so `1 << tier` for tier ≥ 128 is a valid large `uint256`. But `nonDefaultFeeTierMask` is `uint128` and is zero-extended to `uint256` for the AND. Its upper 128 bits are always zero, so the condition is always false for tier ≥ 128. The function unconditionally returns the hardcoded default.

The state variable and the struct field that feeds it both use `uint32`:

```solidity
uint128 internal nonDefaultFeeTierMask;
``` [3](#0-2) 

```solidity
struct UpdateTierFeeRates {
    uint32 tier;
    ...
}
``` [4](#0-3) 

The mismatch between the `uint32` tier space and the `uint128` mask is the root cause.

---

### Impact Explanation

Every trade that involves a user whose fee tier is ≥ 128 calls `getTierFeeRateX18`, which returns the hardcoded default instead of the configured rate:

- Users entitled to a **maker rebate** (negative `makerRateX18`) receive 0 instead — they are undercompensated.
- Users entitled to a **reduced taker fee** receive 2 bps instead — they are overcharged.
- Users subject to a **higher taker fee** (e.g., penalised accounts) receive 2 bps instead — they are undercharged, harming the protocol.

This is a direct, per-trade asset delta affecting every fill for every user assigned tier ≥ 128. The corrupted balance accumulates silently across all trades.

---

### Likelihood Explanation

The sequencer submits `UpdateFeeTier` transactions assigning tiers to users, and the owner submits `UpdateTierFeeRates` to configure rates. Both are normal, intended protocol operations. If the protocol deploys more than 128 fee tiers (e.g., VIP1–VIP200 or per-product tier schedules), tiers ≥ 128 will be assigned and the bug activates automatically. No attacker action is required; the protocol's own sequencer triggers it through legitimate operation.

---

### Recommendation

Replace `uint128 internal nonDefaultFeeTierMask` with a `mapping(uint32 => bool) internal isNonDefaultFeeTier` (or equivalently a `mapping(uint32 => mapping(uint32 => FeeRates))` with a sentinel check). This removes the 128-tier ceiling entirely:

```solidity
// Replace:
uint128 internal nonDefaultFeeTierMask;

// With:
mapping(uint32 => bool) internal nonDefaultFeeTier;
```

Update `updateTierFeeRates` to set `nonDefaultFeeTier[txn.tier] = true` and `getTierFeeRateX18` to check `nonDefaultFeeTier[tier]`. This mirrors the fix suggested in M-03: widen the type to accommodate the full intended range.

---

### Proof of Concept

1. Owner calls `UpdateTierFeeRates` with `tier = 200`, `makerRateX18 = -1e14` (−1 bps rebate), `takerRateX18 = 1e14` (1 bps).
2. Inside `updateTierFeeRates`: `nonDefaultFeeTierMask |= uint128(1) << 200` → `uint128(1) << 200 = 0` → mask unchanged.
3. Sequencer assigns a user to tier 200 via `UpdateFeeTier`.
4. User places a maker order; `getTierFeeRateX18(200, productId)` is called.
5. `nonDefaultFeeTierMask & (1 << 200)` → `nonDefaultFeeTierMask` (uint128, zero-extended) AND `2^200` (uint256) = 0.
6. Function returns `{makerRateX18: 0, takerRateX18: 2e14}` — the default — instead of the configured rebate.
7. The maker receives no rebate; the protocol retains fees it should have paid out.

### Citations

**File:** core/contracts/OffchainExchange.sol (L61-61)
```text
    uint128 internal nonDefaultFeeTierMask;
```

**File:** core/contracts/OffchainExchange.sol (L933-946)
```text
    function getTierFeeRateX18(uint32 tier, uint32 productId)
        public
        view
        returns (FeeRates memory)
    {
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
    }
```

**File:** core/contracts/OffchainExchange.sol (L990-990)
```text
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```

**File:** core/contracts/interfaces/IEndpoint.sol (L238-243)
```text
    struct UpdateTierFeeRates {
        uint32 tier;
        uint32 productId;
        int128 makerRateX18;
        int128 takerRateX18;
    }
```
