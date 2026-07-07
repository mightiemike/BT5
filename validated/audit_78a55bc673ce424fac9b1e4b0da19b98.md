The vulnerability is real and concrete. Here is the full analysis:

---

### Title
Silent Mask Overflow for Tier ≥ 128 Causes Custom Fee Rates to Be Permanently Ignored — (`core/contracts/OffchainExchange.sol`)

### Summary

`nonDefaultFeeTierMask` is a `uint128` (128 bits wide). Both the write path in `updateTierFeeRates` and the read path in `getTierFeeRateX18` perform `uint128(1) << tier`. In Solidity `^0.8.0`, when the shift amount equals or exceeds the bit-width of the type, the result is `0`. For any `tier >= 128`, the mask bit is never set on write and the check always evaluates to `false` on read. The `feeRates` storage mapping is written correctly, but the gate that unlocks it is permanently broken for those tiers.

### Finding Description

**Write path** — `OffchainExchange.sol` line 990:

```solidity
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```

`txn.tier` is `uint32`, so values 128–4,294,967,295 are valid inputs. For `txn.tier >= 128`, `uint128(1) << txn.tier` evaluates to `0` (the bit is shifted out of the 128-bit type). The OR-assignment is a no-op; `nonDefaultFeeTierMask` is unchanged. [1](#0-0) 

**Read path** — `OffchainExchange.sol` line 938:

```solidity
if (nonDefaultFeeTierMask & (1 << tier) != 0) {
```

The untyped literal `1` is resolved to `uint128` by the compiler because the left operand of `&` is `uint128`. For `tier >= 128`, `uint128(1) << tier` is again `0`, so the condition is always `false` and the function falls through to the hardcoded default. [2](#0-1) 

The `feeRates` mapping itself is written correctly for any tier value: [3](#0-2) 

The mask is the sole gate between stored rates and returned rates. Because it is never set for tier ≥ 128, the stored rates are unreachable.

`nonDefaultFeeTierMask` is declared as `uint128`: [4](#0-3) 

`tier` in the struct is `uint32`, permitting values 0–4,294,967,295: [5](#0-4) 

The entry point is `ContractOwner.updateTierFeeRates` (owner-gated), which submits a slow-mode transaction that routes to `OffchainExchange.updateTierFeeRates` via the endpoint: [6](#0-5) 

### Impact Explanation

Any user assigned to a tier ≥ 128 via `updateFeeTier` will always receive the hardcoded default fee schedule (`makerRateX18 = 0`, `takerRateX18 = 200_000_000_000_000` — 2 bps) regardless of what was configured. If the protocol intends to grant zero-fee, reduced-fee, or rebate tiers to high-volume traders using tier numbers ≥ 128, those users are silently overcharged on every trade. The excess fees are collected into `market.collectedFees` and eventually swept to the protocol's X account via `dumpFees`, constituting an incorrect fee transfer from users to the protocol. [7](#0-6) 

### Likelihood Explanation

The `tier` field is `uint32` with no upper-bound validation anywhere in the call chain. The owner can legitimately call `updateTierFeeRates` with `tier = 128` (e.g., to create a VIP tier namespace above the standard 0–127 range) and receive no error. The silent failure is not detectable from the transaction itself — the storage write succeeds, only the mask update is a no-op. This makes the bug easy to trigger accidentally during normal protocol operation.

### Recommendation

Widen `nonDefaultFeeTierMask` to `uint256` (supporting tiers 0–255), or add an explicit upper-bound check in `updateTierFeeRates` that reverts if `txn.tier >= 128`. The simplest fix:

```solidity
// Option A: widen the mask
uint256 internal nonDefaultFeeTierMask;

// Option B: guard the input
require(txn.tier < 128, "tier out of mask range");
```

If `uint256` is chosen, update the read path in `getTierFeeRateX18` to use `uint256(1) << tier` consistently.

### Proof of Concept

```solidity
// 1. Owner calls ContractOwner.updateTierFeeRates with tier=128, takerRateX18=0
// 2. feeRates[128][productId] is written: takerRateX18 = 0
// 3. nonDefaultFeeTierMask |= uint128(1) << 128  =>  |= 0  =>  unchanged (still 0)
// 4. Owner assigns user to tier 128 via updateFeeTier
// 5. User submits a taker order
// 6. getUserFeeRateWithBuilder calls getTierFeeRateX18(128, productId)
// 7. nonDefaultFeeTierMask & (uint128(1) << 128) == 0 & 0 == 0  =>  condition false
// 8. Returns default: takerRateX18 = 200_000_000_000_000 (2 bps)
// 9. User is charged 2 bps instead of 0 bps — incorrect fee transfer
```

### Citations

**File:** core/contracts/OffchainExchange.sol (L61-61)
```text
    uint128 internal nonDefaultFeeTierMask;
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

**File:** core/contracts/OffchainExchange.sol (L984-989)
```text
        } else {
            feeRates[txn.tier][txn.productId] = FeeRates(
                txn.makerRateX18,
                txn.takerRateX18
            );
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

**File:** core/contracts/ContractOwner.sol (L405-420)
```text
    function updateTierFeeRates(
        uint32[] memory tier,
        uint32[] memory productId,
        int128[] memory makerRateX18,
        int128[] memory takerRateX18
    ) external onlyOwner {
        if (
            tier.length != productId.length ||
            tier.length != makerRateX18.length ||
            tier.length != takerRateX18.length
        ) {
            revert InvalidInput();
        }
        for (uint256 i = 0; i < tier.length; i++) {
            IEndpoint.UpdateTierFeeRates memory _txn = IEndpoint
                .UpdateTierFeeRates(
```
