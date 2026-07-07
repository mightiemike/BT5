### Title
`nonDefaultFeeTierMask` Bit Never Set for Tiers ≥ 128 Due to `uint128` Shift Truncation — (`File: core/contracts/OffchainExchange.sol`)

---

### Summary

In `OffchainExchange.sol`, the `nonDefaultFeeTierMask` state variable is declared as `uint128`. When `updateTierFeeRates` is called with a `tier` value ≥ 128, the expression `uint128(1) << txn.tier` silently evaluates to `0` — Solidity does not revert on shift-amount overflow for unsigned integer types. The bit is never set, so `getTierFeeRateX18` always falls through to the hardcoded default rate for that tier, even though custom rates were stored in `feeRates[tier][productId]`.

---

### Finding Description

`nonDefaultFeeTierMask` is declared as `uint128`: [1](#0-0) 

In `updateTierFeeRates`, the mask is updated with:

```solidity
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
``` [2](#0-1) 

`txn.tier` is `uint32`. When `txn.tier >= 128`, `uint128(1) << txn.tier` evaluates to `0` in Solidity (left-shifting a `uint128` by ≥ 128 positions yields zero without reverting). The OR-assignment becomes a no-op and the bit for that tier is never recorded.

The lookup function then checks:

```solidity
if (nonDefaultFeeTierMask & (1 << tier) != 0) {
    return feeRates[tier][productId];
}
return FeeRates({
    makerRateX18: 0,
    takerRateX18: 200_000_000_000_000 // 2 bps
});
``` [3](#0-2) 

Because the bit was never set, the condition is always `false` for tier ≥ 128. The custom `FeeRates` stored in `feeRates[tier][productId]` are permanently unreachable for those tiers.

The `tier` parameter flows from `IEndpoint.UpdateTierFeeRates`, submitted via `ContractOwner.updateTierFeeRates` (owner-gated), which encodes it as `uint32` with no upper-bound validation: [4](#0-3) 

---

### Impact Explanation

Any user assigned to a fee tier ≥ 128 via `updateFeeTier` will always be charged the hardcoded default taker rate of 2 bps, regardless of the custom rate that was configured and stored. If the custom rate is lower (e.g., 0 for a VIP tier), those users are systematically overcharged on every matched order. If the custom rate is higher, the protocol silently under-collects fees. The corrupted state is the `nonDefaultFeeTierMask` variable — the bit that gates access to the stored `FeeRates` is never written, making the stored custom rates permanently dead code for those tiers.

---

### Likelihood Explanation

Fee tiers are owner-configured via `ContractOwner.updateTierFeeRates`. The `tier` field is `uint32` with no on-chain cap. An operator assigning a tier number ≥ 128 (e.g., tier 200 for a new VIP class) would silently produce this failure with no error or event indicating the mask was not updated. The bug is latent and non-obvious because `feeRates[tier][productId]` is correctly written — only the mask lookup is broken.

---

### Recommendation

Change `nonDefaultFeeTierMask` from `uint128` to `uint256` so it can track all valid `uint32` tier values up to 255:

```diff
- uint128 internal nonDefaultFeeTierMask;
+ uint256 internal nonDefaultFeeTierMask;
```

Alternatively, add an explicit bounds check in `updateTierFeeRates`:

```solidity
require(txn.tier < 128, "tier out of range");
```

The analogous fix in the referenced JOJO report changed `uint32 newImpact` to `uint256 newImpact` — the same principle applies here: the type used to index or shift into the mask must be compatible with the mask's bit width.

---

### Proof of Concept

1. Owner calls `ContractOwner.updateTierFeeRates` with `tier = 200`, `productId = 1`, `makerRateX18 = 0`, `takerRateX18 = 0` (zero-fee VIP tier).
2. `feeRates[200][1]` is correctly written to `FeeRates(0, 0)`.
3. `nonDefaultFeeTierMask |= uint128(1) << 200` → `uint128(1) << 200 == 0` → mask unchanged.
4. Owner calls `updateFeeTier(userAddress, 200)` to assign the VIP tier to a trader.
5. Trader submits an order; `matchOrders` calls `getUserFeeRateWithBuilder` → `getTierFeeRateX18(200, 1)`.
6. `nonDefaultFeeTierMask & (1 << 200)` evaluates to `0` (bit was never set).
7. Function returns `FeeRates(0, 200_000_000_000_000)` — the 2 bps default — instead of `FeeRates(0, 0)`.
8. Trader is charged 2 bps on every fill despite being configured for zero fees. [5](#0-4)

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

**File:** core/contracts/ContractOwner.sol (L405-431)
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
                    tier[i],
                    productId[i],
                    makerRateX18[i],
                    takerRateX18[i]
                );
            _submitSlowModeTransaction(
                IEndpoint.TransactionType.UpdateTierFeeRates,
                abi.encode(_txn)
            );
        }
    }
```
