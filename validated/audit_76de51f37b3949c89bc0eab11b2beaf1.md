### Title
New products added after global tier-fee-rate initialization silently inherit zero fee rates for all non-default tiers — (`core/contracts/OffchainExchange.sol`)

---

### Summary

`updateTierFeeRates` called with `productId == QUOTE_PRODUCT_ID` snapshots the current product list, writes `feeRates[tier][productId]` for every product that exists at that moment, and permanently marks the tier as non-default via `nonDefaultFeeTierMask`. Any product added **after** that call is never written into `feeRates[tier][*]`. Because `getTierFeeRateX18` branches on `nonDefaultFeeTierMask` first, it returns the uninitialized `FeeRates(0, 0)` — zero maker **and** zero taker — for every (non-default tier, new product) pair. Every user assigned to a non-default tier pays no fees on those products forever.

---

### Finding Description

**Phase 1 — global tier initialization:**

`updateTierFeeRates` with `txn.productId == QUOTE_PRODUCT_ID` iterates over the product lists at call time and writes fee rates for each existing product, then marks the tier as non-default: [1](#0-0) 

After this call, `nonDefaultFeeTierMask` has bit `tier` set permanently.

**Phase 2 — new product added:**

`updateMarket` registers a new product into `marketInfo[productId]` with its size increment and min size. It does **not** initialize `feeRates[tier][newProductId]` for any tier. The mapping entry remains `FeeRates(0, 0)`.

**Fee lookup at trade time:**

`getUserFeeRateWithBuilder` resolves the caller's fee tier and calls `getTierFeeRateX18`: [2](#0-1) 

`getTierFeeRateX18` checks `nonDefaultFeeTierMask` first. Because the bit is already set from Phase 1, it skips the default-rate branch and returns the raw mapping value: [3](#0-2) 

For the new product, `feeRates[tier][newProductId]` was never written, so it returns `FeeRates(makerRateX18: 0, takerRateX18: 0)`.

The analog to the external report is exact:

| External report | Nado analog |
|---|---|
| `referred_by_id` NULL at INSERT; trigger fires at INSERT only | `feeRates[tier][productId]` unset at product creation; `nonDefaultFeeTierMask` set at tier-init only |
| `referral_percents` stays at schema default forever | `feeRates[tier][newProduct]` stays at zero forever |
| Overpayment on every trade by V | Underpayment (zero fee) on every trade on the new product |

---

### Impact Explanation

Every user whose `feeTiers[address]` maps to any non-default tier pays **zero maker and zero taker fees** on every newly listed product. The protocol's `collectedFees` for those (tier, product) pairs accrues nothing. The error is silent — no revert, no event anomaly — and affects every trade on the new product by every non-default-tier user until an explicit per-product `updateTierFeeRates` call is made. Historical underpayments are not recoverable.

**Impact: Medium** — continuous fee revenue loss on new products for all non-default-tier users; no direct asset theft, but meaningful and silent accounting corruption.

---

### Likelihood Explanation

The protocol is designed to list new products over time. Non-default fee tiers are assigned to active traders. The global `QUOTE_PRODUCT_ID` path of `updateTierFeeRates` is the natural way to configure a tier across all markets. The ordering (tier configured → new product added) is the normal operational sequence. No attacker action is required; any non-default-tier user trading on a newly listed product triggers the zero-fee path automatically.

**Likelihood: Medium** — occurs on every new product listing after any global tier-fee-rate update, which is a routine operational event.

---

### Recommendation

When `updateMarket` registers a new product, initialize `feeRates[tier][newProductId]` for every tier that already has its bit set in `nonDefaultFeeTierMask`. Alternatively, change `getTierFeeRateX18` to fall back to the default rate when `feeRates[tier][productId]` is the zero struct, even if the tier bit is set:

```solidity
function getTierFeeRateX18(uint32 tier, uint32 productId)
    public view returns (FeeRates memory)
{
    if (nonDefaultFeeTierMask & (1 << tier) != 0) {
        FeeRates memory r = feeRates[tier][productId];
        // Fall back to default if this product was never initialized for this tier
        if (r.makerRateX18 != 0 || r.takerRateX18 != 0) {
            return r;
        }
    }
    return FeeRates({ makerRateX18: 0, takerRateX18: 200_000_000_000_000 });
}
```

The longer-term fix mirrors the external report's recommendation: when `updateTierFeeRates` is called with `QUOTE_PRODUCT_ID`, store the intended rates in a per-tier default (`tierDefaultRates[tier]`) and have `getTierFeeRateX18` consult that default when a product-specific entry is absent.

---

### Proof of Concept

1. Owner calls `updateTierFeeRates({ tier: 5, productId: QUOTE_PRODUCT_ID, makerRateX18: X, takerRateX18: Y })`.
   - `feeRates[5][product1..N]` = `(X, Y)` for all currently listed products.
   - `nonDefaultFeeTierMask |= 1 << 5`. [4](#0-3) 

2. Owner calls `updateMarket(newProductId, quoteId, sizeIncrement, minSize)`.
   - `feeRates[5][newProductId]` is never written → remains `FeeRates(0, 0)`.

3. Owner calls `updateFeeTier(alice, 5)` → `feeTiers[alice] = 5`. [5](#0-4) 

4. Alice submits a trade on `newProductId` via `matchOrders`.
   - `getUserFeeRateWithBuilder` reads `feeTiers[alice] = 5`. [6](#0-5) 
   - `getTierFeeRateX18(5, newProductId)`: `nonDefaultFeeTierMask & (1<<5) != 0` → returns `feeRates[5][newProductId]` = `FeeRates(0, 0)`. [7](#0-6) 
   - Alice pays zero fees. Protocol accrues nothing.

### Citations

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

**File:** core/contracts/OffchainExchange.sol (L952-959)
```text
    function updateFeeTier(address user, uint32 newTier) external {
        require(msg.sender == address(clearinghouse), ERR_UNAUTHORIZED);
        if (newTier != 0 && !addressTouched[user]) {
            addressTouched[user] = true;
            customFeeAddresses.push(user);
        }
        feeTiers[user] = newTier;
        emit FeeTierUpdate(user, newTier);
```

**File:** core/contracts/OffchainExchange.sol (L966-990)
```text
        if (txn.productId == QUOTE_PRODUCT_ID) {
            uint32[] memory spotProductIds = spotEngine.getProductIds();
            uint32[] memory perpProductIds = perpEngine.getProductIds();
            for (uint32 i = 0; i < spotProductIds.length; i++) {
                if (spotProductIds[i] == QUOTE_PRODUCT_ID) {
                    continue;
                }
                feeRates[txn.tier][spotProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
            for (uint32 i = 0; i < perpProductIds.length; i++) {
                feeRates[txn.tier][perpProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
        } else {
            feeRates[txn.tier][txn.productId] = FeeRates(
                txn.makerRateX18,
                txn.takerRateX18
            );
        }
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```
