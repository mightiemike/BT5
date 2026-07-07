Now I have all the information needed. Let me confirm the exact types and the two-sided nature of the bug.

### Title
Unsafe Bit-Shift Truncation in `nonDefaultFeeTierMask` Causes Fee Tier Lookup to Always Fall Back to Default — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.updateTierFeeRates` uses `uint128(1) << txn.tier` to mark a tier as non-default in `nonDefaultFeeTierMask`. When `txn.tier >= 128`, Solidity 0.8.x evaluates this shift to `0`, so the mask bit is never set. The symmetric read in `getTierFeeRateX18` then always falls through to the hardcoded default (0 maker / 2 bps taker), silently ignoring every custom fee rate stored for that tier. Any user assigned to a tier ≥ 128 is permanently mispriced on every trade.

---

### Finding Description

`nonDefaultFeeTierMask` is declared as `uint128`:

```solidity
// OffchainExchange.sol line 61
uint128 internal nonDefaultFeeTierMask;
``` [1](#0-0) 

`UpdateTierFeeRates.tier` is `uint32`:

```solidity
// IEndpoint.sol lines 238-243
struct UpdateTierFeeRates {
    uint32 tier;
    uint32 productId;
    int128 makerRateX18;
    int128 takerRateX18;
}
``` [2](#0-1) 

**Write side — `updateTierFeeRates` line 990:**

```solidity
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
``` [3](#0-2) 

In Solidity ≥ 0.8.0, shifting a typed value by an amount ≥ its bit-width yields `0`. When `txn.tier >= 128`, `uint128(1) << txn.tier` is `0`, so the `|=` is a no-op and the mask bit for that tier is never recorded.

**Read side — `getTierFeeRateX18` line 938:**

```solidity
if (nonDefaultFeeTierMask & (1 << tier) != 0) {
    return feeRates[tier][productId];
}
return FeeRates({ makerRateX18: 0, takerRateX18: 200_000_000_000_000 });
``` [4](#0-3) 

The literal `1` is `uint256`, so `1 << tier` for `tier >= 128` is a non-zero `uint256` with bits set above position 127. When ANDed with `nonDefaultFeeTierMask` (a `uint128` zero-extended to `uint256`), bits 128+ are always 0, so the condition is always `false`. The custom rates stored in `feeRates[tier][productId]` are permanently unreachable.

The two-sided failure mirrors the external report exactly: the write silently drops the bit (analogous to `invalidatorBit` becoming 0), and the read can never observe it.

---

### Impact Explanation

Every call to `matchOrders` invokes `applyFee → getUserFeeRateWithBuilder → getTierFeeRateX18`. [5](#0-4) 

For any user whose `feeTiers[address]` maps to a tier ≥ 128, the returned rate is always the hardcoded default (2 bps taker, 0 maker) regardless of what was configured. If the intended custom rate is lower (e.g., 0 bps for a VIP tier), the user is overcharged on every fill. If the intended rate is higher, the protocol under-collects fees. The delta accumulates across every matched order for every affected user.

---

### Likelihood Explanation

**Low.** The trigger requires the sequencer to have issued an `UpdateTierFeeRates` transaction with `tier >= 128` and at least one user to have been assigned that tier via `UpdateFeeTier`. Both are sequencer-controlled operations. However, `tier` is typed `uint32` (max ~4 billion), there is no on-chain guard preventing values ≥ 128, and the failure is completely silent — the transaction succeeds, the fee rates are stored, but the mask is never updated. A protocol operator expanding to many fee tiers would encounter this without any error signal.

---

### Recommendation

1. **Write side**: Replace the unsafe shift with a checked expression or use a `uint256` mask:
   ```solidity
   // Option A: use uint256 mask (supports up to 256 tiers)
   uint256 internal nonDefaultFeeTierMask;
   nonDefaultFeeTierMask |= uint256(1) << txn.tier;

   // Option B: enforce tier < 128 at the call site
   require(txn.tier < 128, "tier out of range");
   nonDefaultFeeTierMask |= uint128(1) << txn.tier;
   ```

2. **Read side**: Ensure the mask type and the shift type are consistent so the check cannot silently fail.

---

### Proof of Concept

```solidity
// Sequencer calls updateTierFeeRates with tier = 128
// txn.tier = 128 (uint32)
// uint128(1) << 128 == 0  (Solidity 0.8.x: shift >= bit-width → 0)
// nonDefaultFeeTierMask unchanged (still 0 or whatever it was)

// feeRates[128][productId] IS written correctly — the storage update succeeds.

// Later, matchOrders calls getTierFeeRateX18(128, productId):
// nonDefaultFeeTierMask & (1 << 128)
//   = uint128_value_zero_extended & (uint256 with bit 128 set)
//   = 0  (bit 128 is always 0 in a uint128 zero-extended to uint256)
// → condition false → returns default 2 bps taker rate
// → custom rate in feeRates[128][productId] is never used

// User assigned to tier 128 is charged 2 bps on every trade
// instead of their configured custom rate.
```

### Citations

**File:** core/contracts/OffchainExchange.sol (L61-61)
```text
    uint128 internal nonDefaultFeeTierMask;
```

**File:** core/contracts/OffchainExchange.sol (L549-554)
```text
        FeeInfo memory feeInfo = getUserFeeRateWithBuilder(
            orderInfo.sender,
            productId,
            appendix,
            taker
        );
```

**File:** core/contracts/OffchainExchange.sol (L938-945)
```text
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
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
