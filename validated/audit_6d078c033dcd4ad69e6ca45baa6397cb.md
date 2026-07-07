Looking at the actual code, I need to trace the exact computation path for `alreadyMatched` in `matchOrders` and verify whether the invariant described in the question can actually break.

**Key findings from the code:**

**1. TWAP orders can have `priceX18 = 0`** — confirmed at line 458:
```solidity
return ((order.priceX18 > 0) || _isTWAP(order.appendix)) && ...
``` [1](#0-0) 

**2. `_isTWAP` is a pure appendix bit-check** — no price constraint: [2](#0-1) 

**3. The price-crossing check with `priceX18=0`** — if taker has `priceX18=0`, the check `maker.order.priceX18 >= taker.order.priceX18` (line 734) passes for any positive maker price when `maker.order.amount > 0`. The reverse branch (`maker.order.priceX18 <= 0`) would fail for a selling maker. So TWAP takers with `priceX18=0` can only be matched with buying makers (taker is selling). [3](#0-2) 

**4. The exact `alreadyMatched` computation — the confirmed bug:**
```solidity
applyFee(
    callState.productId,
    ordersInfo.taker,
    market,
    -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
    taker.order.appendix,
    true
);
``` [4](#0-3) 

This uses the **current** `maker.order.priceX18` multiplied by the cumulative `filledAmounts[taker.digest]`. It does **not** track the actual quote paid in prior fills.

**5. `filledAmounts` only tracks base amounts, not quote:** [5](#0-4) 

**6. How `alreadyMatched` is consumed in `applyFee`:**
```solidity
int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) - market.minSize;
feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
``` [6](#0-5) 

The `alreadyMatched` determines how much of the current fill is above the `minSize` fee-exemption threshold.

---

**Concrete error trace:**

- Fill 1: maker price = P1, taker fills A1 base → actual quote = P1·|A1|
  - `alreadyMatched = -P1 · 0 = 0` ✓ (correct, no prior fills)
  - `filledAmounts[digest]` becomes A1 (negative, taker selling)

- Fill 2: maker price = P2 ≪ P1, taker fills A2 base
  - **Wrong:** `alreadyMatched = -P2 · A1`
  - **Correct:** `alreadyMatched = -P1 · A1` (actual quote received in fill 1)
  - Error = `(P1 - P2) · |A1|`

**Concrete undercharge example:**
- `minSize = 100 USDC`, P1 = 100, A1 = 2 → fill 1 quote = 200 USDC
- P2 = 10, A2 = 2 → fill 2 quote = 20 USDC
  - Wrong `alreadyMatched = -10 · 2 = -20`
  - `feeApplied = abs(-20 + (-20)) - 100 = 40 - 100 = -60` → clamped to 0 → **zero fee**
  - Correct `alreadyMatched = -200`
  - `feeApplied = abs(-200 + (-20)) - 100 = 120`, clamped to `min(120, 20) = 20` → **fee = 20 · feeRate**

The taker pays zero fees on fill 2 when they should pay fees on the full 20 USDC.

---

**However, the impact is structurally bounded:**

The error only affects the `minSize` exemption window. Once `|alreadyMatched + matchQuote| > minSize` under both the correct and wrong values, the fee is clamped to `matchQuote.abs()` and the error disappears. The miscalculation is only material when the wrong `alreadyMatched` places the cumulative quote below `minSize` while the correct value places it above. The maximum fee underpayment per fill is `matchQuote.abs() · feeRate` (the entire fill's fee), but this only occurs near the `minSize` threshold crossing.

For large TWAP orders where fills individually exceed `minSize`, the error is zero after the first fill. The vulnerability is real but **not a systematic drain** — it is a bounded, per-order fee miscalculation near the `minSize` threshold, not a protocol-wide solvency or undercollateralization risk.

---

### Title
TWAP Taker `alreadyMatched` Uses Current Maker Price Instead of Actual Prior Quote, Causing Bounded Fee Miscalculation Near `minSize` Threshold — (`core/contracts/OffchainExchange.sol`)

### Summary
In `matchOrders`, the `alreadyMatched` parameter passed to `applyFee` for the taker is computed as `-maker.order.priceX18 · filledAmounts[taker.digest]`. For TWAP orders (which may have `priceX18=0` and are filled across multiple makers at different prices), this uses the **current** maker's price rather than the actual cumulative quote paid in prior fills. The result is a wrong `alreadyMatched` value that can cause the `minSize` fee exemption to be applied incorrectly.

### Finding Description
`filledAmounts[digest]` stores cumulative **base** amounts filled. The correct `alreadyMatched` (cumulative quote paid) would require storing cumulative quote amounts separately. Instead, the code approximates it by multiplying the current maker's price by the cumulative base amount. When a TWAP order is filled at prices P1, P2, ..., PN, the `alreadyMatched` for fill k is `-Pk · Σ(A1..A(k-1))` instead of `-Σ(Pi·Ai)` for i < k. The error per fill is `Σ(i<k) Ai·(Pi - Pk)`.

### Impact Explanation
When the current maker price is much lower than prior fill prices, `alreadyMatched` is less negative than the true cumulative quote, making the protocol believe the taker is still below the `minSize` threshold. This causes `feeApplied` to be zero (or reduced) when it should be positive, resulting in fee underpayment. The maximum underpayment per affected fill is `matchQuote.abs() · feeRate`. This is bounded and does not cause undercollateralization, but it does reduce `collectedFees

### Citations

**File:** core/contracts/OffchainExchange.sol (L379-382)
```text
    function _isTWAP(uint128 appendix) internal pure returns (bool) {
        uint128 trigger = (appendix >> 12) & 3;
        return trigger >= 2;
    }
```

**File:** core/contracts/OffchainExchange.sol (L457-458)
```text
        return
            ((order.priceX18 > 0) || _isTWAP(order.appendix)) &&
```

**File:** core/contracts/OffchainExchange.sol (L536-538)
```text
            int128 feeApplied = MathHelper.abs(alreadyMatched + matchQuote) -
                market.minSize;
            feeApplied = MathHelper.min(feeApplied, matchQuote.abs());
```

**File:** core/contracts/OffchainExchange.sol (L732-742)
```text
        if (maker.order.amount > 0) {
            require(
                maker.order.priceX18 >= taker.order.priceX18,
                ERR_ORDERS_CANNOT_BE_MATCHED
            );
        } else {
            require(
                maker.order.priceX18 <= taker.order.priceX18,
                ERR_ORDERS_CANNOT_BE_MATCHED
            );
        }
```

**File:** core/contracts/OffchainExchange.sol (L770-777)
```text
        applyFee(
            callState.productId,
            ordersInfo.taker,
            market,
            -maker.order.priceX18.mul(filledAmounts[ordersInfo.taker.digest]),
            taker.order.appendix,
            true
        );
```

**File:** core/contracts/OffchainExchange.sol (L832-835)
```text
            filledAmounts[ordersInfo.taker.digest] += ordersInfo
                .taker
                .amountDelta;
        }
```
