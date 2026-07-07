### Title
`nonDefaultFeeTierMask` Declared as `uint128` Cannot Track Fee Tiers ≥ 128, Silently Discarding Custom Rate Assignments — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`OffchainExchange.sol` stores the set of non-default fee tiers in a `uint128` bitmask. Because `tier` is typed as `uint32` (range 0–4,294,967,295), any tier value ≥ 128 causes the bit-set operation to shift by ≥ 128 positions on a `uint128`, which Solidity 0.8.x silently evaluates to zero. The mask is never updated, so `getTierFeeRateX18` always falls through to the hardcoded default rate for those tiers, regardless of what was written to `feeRates[tier][productId]`.

---

### Finding Description

**State variable declaration** — `nonDefaultFeeTierMask` is `uint128`: [1](#0-0) 

**Mask update in `updateTierFeeRates`** — the shift is performed on a `uint128` operand: [2](#0-1) 

When `txn.tier >= 128`, `uint128(1) << txn.tier` evaluates to `0` in Solidity 0.8.x (no revert; the EVM simply returns zero for over-width shifts). The `|= 0` is a no-op, so the bit for that tier is never set.

**Mask read in `getTierFeeRateX18`** — the same zero result occurs on the read side: [3](#0-2) 

`nonDefaultFeeTierMask & (1 << tier)` is also zero for `tier >= 128`, so the function always returns the hardcoded default `{ makerRateX18: 0, takerRateX18: 200_000_000_000_000 }` — the custom rates stored in `feeRates[tier][productId]` are permanently unreachable.

The `tier` field in `UpdateTierFeeRates` is `uint32`: [4](#0-3) 

---

### Impact Explanation

Any fee tier ≥ 128 that is configured via `updateTierFeeRates` is silently inert. Users assigned to those tiers via `UpdateFeeTier` will always be charged the default 2 bps taker rate and 0 maker rebate, regardless of the custom rates that were written. This corrupts the fee accounting invariant: `feeRates[tier][productId]` holds a value that can never be read back through the normal lookup path. Depending on whether the custom rate is lower or higher than the default, affected traders either overpay fees or receive no maker rebate they were entitled to — a direct, per-trade asset delta.

---

### Likelihood Explanation

The `tier` field is `uint32` with no enforced upper bound in the contract. The protocol's `updateTierFeeRates` slow-mode path accepts any `uint32` tier value. If the operator ever assigns a tier ≥ 128 (e.g., as part of a tiered VIP scheme with many levels), the silent failure activates immediately for every subsequent order matched for users on that tier. The bug requires no attacker; it is triggered by a legitimate, well-formed owner transaction that the contract accepts without error.

---

### Recommendation

Change `nonDefaultFeeTierMask` from `uint128` to `uint256`:

```solidity
// Before
uint128 internal nonDefaultFeeTierMask;

// After
uint256 internal nonDefaultFeeTierMask;
```

Update the shift expression accordingly:

```solidity
// Before
nonDefaultFeeTierMask |= uint128(1) << txn.tier;

// After
nonDefaultFeeTierMask |= uint256(1) << txn.tier;
```

`uint256` supports 256 distinct tier bits, which is sufficient for any realistic tier numbering scheme. Alternatively, enforce `require(txn.tier < 128)` if the 128-tier limit is intentional and document it explicitly.

---

### Proof of Concept

1. Owner calls `updateTierFeeRates` with `tier = 200`, `productId = 1`, `makerRateX18 = -500_000_000_000_000` (−5 bps rebate), `takerRateX18 = 100_000_000_000_000` (1 bps).
2. Inside `updateTierFeeRates`, `feeRates[200][1]` is written correctly.
3. `nonDefaultFeeTierMask |= uint128(1) << 200` → `uint128(1) << 200 = 0` → mask unchanged.
4. Owner calls `UpdateFeeTier` to assign `user = Alice`, `newTier = 200`.
5. Alice submits a taker order. `matchOrders` → `applyFee` → `getUserFeeRateWithBuilder` → `getTierFeeRateX18(200, 1)`.
6. `nonDefaultFeeTierMask & (uint128(1) << 200)` = `nonDefaultFeeTierMask & 0` = `0` → condition false.
7. Returns `{ makerRateX18: 0, takerRateX18: 200_000_000_000_000 }` (default 2 bps) instead of Alice's custom 1 bps rate.
8. Alice is overcharged on every trade; the custom rate written to storage is permanently inaccessible.

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

**File:** core/contracts/OffchainExchange.sol (L988-991)
```text
            );
        }
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
    }
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
