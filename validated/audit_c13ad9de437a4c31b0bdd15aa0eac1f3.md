### Title
Per-Product Fee Rate Granularity Lost Due to Tier-Level Sentinel Mask — (`core/contracts/OffchainExchange.sol`)

### Summary
`nonDefaultFeeTierMask` tracks whether a fee tier has been configured at the tier level, but `feeRates` is a `tier × productId` mapping. When `updateTierFeeRates` is called for a single specific product, the tier bit is set in `nonDefaultFeeTierMask`, causing `getTierFeeRateX18` to return `feeRates[tier][productId]` for ALL products under that tier — including products never explicitly configured, whose storage value is `FeeRates(0, 0)`. Users in that tier pay zero fees on all unconfigured products instead of the default 2 bps taker rate.

### Finding Description

`getTierFeeRateX18` uses `nonDefaultFeeTierMask` as a sentinel to decide whether to return the stored rate or the hardcoded default:

```solidity
// OffchainExchange.sol L933-946
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        return feeRates[tier][productId];   // returns (0,0) for unconfigured products
    }
    return FeeRates({
        makerRateX18: 0,
        takerRateX18: 200_000_000_000_000   // 2 bps default
    });
}
```

`updateTierFeeRates` sets the tier bit unconditionally, regardless of whether it configured all products or just one:

```solidity
// OffchainExchange.sol L984-990
} else {
    feeRates[txn.tier][txn.productId] = FeeRates(txn.makerRateX18, txn.takerRateX18);
}
nonDefaultFeeTierMask |= uint128(1) << txn.tier;   // marks entire tier as "non-default"
```

The sentinel cannot distinguish between:
- **"tier configured for product A, product B never configured"** → `feeRates[tier][B]` = `FeeRates(0, 0)` (uninitialized)
- **"tier explicitly configured for product B with zero rates"** → `feeRates[tier][B]` = `FeeRates(0, 0)` (intentional)

Both cases return `FeeRates(0, 0)` for product B, so the fallback to the 2 bps default never fires for product B once the tier bit is set.

### Impact Explanation

Protocol fee revenue is silently zeroed for all `(tier, product)` pairs where the tier has been partially configured. Any user assigned to a non-default tier pays zero taker fees on every product not explicitly listed in a `updateTierFeeRates` call for that tier. The `collectedFees` for those products accumulates nothing, permanently reducing protocol revenue. The broken invariant is: `feeRates[tier][product] == FeeRates(0,0)` should mean "use default" but is treated as "explicitly zero" once the tier bit is set.

### Likelihood Explanation

The `updateTierFeeRates` path that triggers this is the product-specific branch (`txn.productId != QUOTE_PRODUCT_ID`). This is a normal operational action — configuring a custom rate for one product on a VIP tier. As soon as any such call is made, all users in that tier trading any other product are affected. New products added to the protocol after a tier is configured are also silently zero-fee for all non-default tiers. Likelihood is high given normal protocol operation.

### Recommendation

Track configuration at the `(tier, productId)` granularity rather than at the tier level. Replace the single `nonDefaultFeeTierMask` with a `mapping(uint32 => mapping(uint32 => bool)) internal tierProductConfigured` (or a `mapping(uint32 => uint256) internal nonDefaultProductMask` per tier). In `getTierFeeRateX18`, check `tierProductConfigured[tier][productId]` before returning `feeRates[tier][productId]`, so that unconfigured `(tier, product)` pairs always fall back to the 2 bps default.

### Proof of Concept

1. Admin calls `updateTierFeeRates({tier: 1, productId: 3, makerRateX18: 100_000_000_000_000, takerRateX18: 150_000_000_000_000})` — configures tier 1 for product 3 only. `nonDefaultFeeTierMask` becomes `0b10` (bit 1 set).
2. Admin calls `updateFeeTier(alice, 1)` — assigns Alice to tier 1.
3. Alice submits a taker order on product 5 (never configured for tier 1).
4. `getUserFeeRateWithBuilder` reads `feeTiers[alice] = 1`, calls `getTierFeeRateX18(1, 5)`.
5. `nonDefaultFeeTierMask & (1 << 1) != 0` → **true** → returns `feeRates[1][5]` = `FeeRates(0, 0)`.
6. `applyFee` computes `feeRate = 0`, Alice pays **zero taker fees** on product 5.
7. The default 2 bps path is never reached; `market.collectedFees` for product 5 receives nothing from Alice's trades. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/OffchainExchange.sol (L36-61)
```text
    mapping(uint32 => mapping(uint32 => FeeRates)) internal feeRates;

    // address -> fee tiers
    mapping(address => uint32) internal feeTiers;
    mapping(address => bool) internal addressTouched;
    address[] internal customFeeAddresses;

    mapping(uint32 => uint32) internal quoteIds;

    // address -> mask (if the i-th bit is 1, it means the i-th iso subacc is being used)
    mapping(address => uint256) internal isolatedSubaccountsMask;

    // isolated subaccount -> subaccount
    mapping(bytes32 => bytes32) internal parentSubaccounts;

    // (subaccount, id) -> isolated subaccount
    mapping(bytes32 => mapping(uint256 => bytes32))
        internal isolatedSubaccounts;

    // which isolated subaccount does an isolated order create
    mapping(bytes32 => bytes32) internal digestToSubaccount;

    // how much margin does an isolated order require
    mapping(bytes32 => int128) internal digestToMargin;

    uint128 internal nonDefaultFeeTierMask;
```

**File:** core/contracts/OffchainExchange.sol (L498-506)
```text
        uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
        if (feeTier < builder.defaultFeeTier) {
            feeTier = builder.defaultFeeTier;
        }
        FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
        int128 feeRate = taker
            ? userFeeRates.takerRateX18
            : userFeeRates.makerRateX18;
        return FeeInfo(feeRate, builderId, builderFeeRate);
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
