### Title
Non-Default Fee Tier Users Pay Zero Fees on Products Without Explicit Rate Configuration - (`core/contracts/OffchainExchange.sol`)

### Summary

`getTierFeeRateX18` uses a single bitmask (`nonDefaultFeeTierMask`) to decide whether to return a stored `feeRates[tier][productId]` value or the hardcoded default (2 bps taker). Once any product's rate is configured for a tier, the mask bit is permanently set for that entire tier. Any subsequent product added to the protocol that is not explicitly configured for that tier will return `{makerRateX18: 0, takerRateX18: 0}` — zero fees — for all users assigned to that tier.

### Finding Description

`getTierFeeRateX18` in `OffchainExchange.sol` resolves fee rates as follows:

```solidity
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        return feeRates[tier][productId];   // returns {0,0} if never set
    }
    return FeeRates({
        makerRateX18: 0,
        takerRateX18: 200_000_000_000_000   // 2 bps default
    });
}
``` [1](#0-0) 

The `nonDefaultFeeTierMask` bit for a tier is set at the tier level, not at the `(tier, productId)` level:

```solidity
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
``` [2](#0-1) 

When `updateTierFeeRates` is called with a specific `productId` (not `QUOTE_PRODUCT_ID`), it only populates `feeRates[tier][productId]` for that one product:

```solidity
} else {
    feeRates[txn.tier][txn.productId] = FeeRates(txn.makerRateX18, txn.takerRateX18);
}
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
``` [3](#0-2) 

Even when the bulk path (`productId == QUOTE_PRODUCT_ID`) is used, it only iterates over products that exist at the time of the call:

```solidity
uint32[] memory spotProductIds = spotEngine.getProductIds();
uint32[] memory perpProductIds = perpEngine.getProductIds();
``` [4](#0-3) 

Any product added after this call will have `feeRates[tier][newProductId]` uninitialized (Solidity default: `{0, 0}`). Because the mask bit for that tier is already set, `getTierFeeRateX18` will return `{0, 0}` instead of the 2 bps default for all users on that tier trading the new product.

The fee rate is consumed in `getUserFeeRateWithBuilder`, which feeds directly into `applyFee`:

```solidity
uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
...
FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
int128 feeRate = taker ? userFeeRates.takerRateX18 : userFeeRates.makerRateX18;
``` [5](#0-4) 

A zero `feeRate` means `keepRateX18 = ONE - 0 = ONE`, so `orderInfo.fee = 0` — the protocol collects nothing.

### Impact Explanation

Any user assigned a non-default fee tier (tier ≠ 0) trades on any newly listed product without paying taker or maker fees. The `marketInfo[productId].collectedFees` accumulator for that product remains zero for all such trades, permanently depriving the protocol of fee revenue on that market for those users. This is a direct protocol revenue loss with no compensating mechanism.

### Likelihood Explanation

The protocol is designed to list new spot and perp products over time. Fee tier assignment is a normal operational action (market makers, VIP users). The window between a new product listing and an admin calling `updateTierFeeRates` for every existing non-default tier is a reliable gap. A user with a non-default tier needs only to submit orders on the new product through the normal sequencer flow — no special permissions or exploits required. The sequencer matches orders and calls `applyFee`, which silently applies a zero rate.

### Recommendation

Decouple the mask granularity from the lookup granularity. Either:

1. **Use a per-`(tier, productId)` initialized flag** instead of a per-tier bitmask, so that an uninitialized `(tier, productId)` pair always falls back to the hardcoded default.
2. **Fall back to the default rate when `feeRates[tier][productId]` is zero** for the taker rate:

```solidity
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        FeeRates memory stored = feeRates[tier][productId];
        if (stored.takerRateX18 != 0 || stored.makerRateX18 != 0) {
            return stored;
        }
    }
    return FeeRates({ makerRateX18: 0, takerRateX18: 200_000_000_000_000 });
}
```

3. **Require `updateTierFeeRates` to be called for all existing non-default tiers when a new product is listed**, enforced on-chain in the product registration path.

### Proof of Concept

1. Admin calls `updateTierFeeRates(tier=1, productId=2, makerRate=X, takerRate=Y)`.
   - `feeRates[1][2]` is set; `nonDefaultFeeTierMask` bit 1 is set.
2. Admin assigns Alice to fee tier 1 via `updateFeeTier(alice, 1)`.
3. A new perp product with `productId=5` is listed. Admin does not call `updateTierFeeRates` for tier 1 on product 5.
4. Alice submits a taker order on product 5. The sequencer matches it.
5. `getUserFeeRateWithBuilder(alice_subaccount, 5, ...)` → `feeTier = 1` → `getTierFeeRateX18(1, 5)`.
6. `nonDefaultFeeTierMask & (1 << 1) != 0` → TRUE → returns `feeRates[1][5]` = `{0, 0}`.
7. `feeRate = 0` → `keepRateX18 = ONE` → `orderInfo.fee = 0`.
8. Alice pays zero taker fees. `marketInfo[5].collectedFees` is never incremented for Alice's trades. [6](#0-5) [7](#0-6)

### Citations

**File:** core/contracts/OffchainExchange.sol (L36-39)
```text
    mapping(uint32 => mapping(uint32 => FeeRates)) internal feeRates;

    // address -> fee tiers
    mapping(address => uint32) internal feeTiers;
```

**File:** core/contracts/OffchainExchange.sol (L61-61)
```text
    uint128 internal nonDefaultFeeTierMask;
```

**File:** core/contracts/OffchainExchange.sol (L498-505)
```text
        uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
        if (feeTier < builder.defaultFeeTier) {
            feeTier = builder.defaultFeeTier;
        }
        FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
        int128 feeRate = taker
            ? userFeeRates.takerRateX18
            : userFeeRates.makerRateX18;
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

**File:** core/contracts/OffchainExchange.sol (L967-968)
```text
            uint32[] memory spotProductIds = spotEngine.getProductIds();
            uint32[] memory perpProductIds = perpEngine.getProductIds();
```

**File:** core/contracts/OffchainExchange.sol (L984-990)
```text
        } else {
            feeRates[txn.tier][txn.productId] = FeeRates(
                txn.makerRateX18,
                txn.takerRateX18
            );
        }
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```
