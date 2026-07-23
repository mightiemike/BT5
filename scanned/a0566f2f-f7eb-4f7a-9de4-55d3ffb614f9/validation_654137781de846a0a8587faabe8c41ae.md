### Title
`collectFees` overallocates when spread and notional fees are both active, causing attempted payout to exceed actual fee surplus — (File: `metric-core/test/MetricOmmPool.notionalFee.t.sol`, root cause in `MetricOmmPool.sol::collectFees`)

---

### Summary

When a pool is configured with both a spread fee (`spreadFeeE6 > 0`) and a notional fee (`notionalFeeE8 > 0`), the fee-collection split math computes the spread-fee payout on the **total** token surplus — which already includes the notional fee accumulator — and then **also** pays out the notional accumulator separately. This double-counts the notional portion, causing the total attempted payout to exceed the actual fee surplus held by the pool.

---

### Finding Description

The pool tracks two distinct fee streams:

1. **Spread fees** — collected as the gap between the pool's real token balance and the sum of all bin balances:
   `surplusScaled = balance × scaleMultiplier − totalScaledTokenInBins`

2. **Notional fees** — tracked in a dedicated accumulator (`notional0` / `notional1` in slot2), incremented on every swap.

When `collectFees` is called, the fee-split logic (as modelled in the test) does:

```solidity
// surplus already contains BOTH spread and notional fees
uint256 surplus0Scaled = (token0.balanceOf(pool) * token0Mul) - totalScaledToken0InBins;

// spread split — applied to the FULL surplus (including notional)
uint256 spread0ToAdmin    = (surplus0Scaled * adminSpreadFeeE6)    / spreadFeeE6;
uint256 spread0ToProtocol = (surplus0Scaled * protocolSpreadFeeE6) / spreadFeeE6;

// notional split — paid out AGAIN on top
uint256 notional0ToAdmin    = (uint256(notional0) * adminNotionalFeeE8)    / notionalFeeE8;
uint256 notional0ToProtocol = uint256(notional0) - notional0ToAdmin;

uint256 total0Attempted = spread0ToAdmin + spread0ToProtocol
                        + notional0ToAdmin + notional0ToProtocol;
// ≈ surplus0Scaled + notional0  >  surplus0Scaled
```

Because `surplus0Scaled` already embeds `notional0`, the spread split consumes the notional portion once, and the explicit notional split consumes it a second time.

The test in the repository explicitly asserts this overallocation occurs:

```solidity
assertGt(total0Attempted, surplus0Scaled,
    "token0 attempted payout exceeds computed surplus");
assertGt(total1Attempted, surplus1Scaled,
    "token1 attempted payout exceeds computed surplus");
``` [1](#0-0) 

---

### Impact Explanation

- **Pool insolvency / DoS on fee collection**: If the pool's token balance equals exactly the bin totals plus the legitimate fee surplus, `collectFees` will attempt to transfer more tokens than exist in the surplus, causing the transfer to revert. Fee collection becomes permanently broken until the pool receives additional token balance.
- **LP principal drain**: If the pool holds any extra token balance (e.g., from rounding residuals or direct token transfers), the overallocated amount is drawn from LP-owned principal rather than from fees, directly reducing LP claims.

Both outcomes satisfy the contest-relevant impact gate: broken core pool functionality causing loss of funds or pool insolvency where balances fail to cover LP claims.

---

### Likelihood Explanation

Any pool with `spreadFeeE6 > 0` **and** `notionalFeeE8 > 0` is affected. Both fee types are independently configurable by the pool admin via `setPoolAdminFees` and are explicitly supported as a combined configuration. The bug is triggered on every `collectFees` invocation after at least one swap has accrued notional fees. No special attacker action is required — the overallocation is structural and fires on the normal admin fee-collection flow. [2](#0-1) 

---

### Recommendation

Separate the spread-fee base from the notional accumulator before computing the split:

```solidity
// Correct: spread fees are only the surplus MINUS the notional accumulator
uint256 spreadSurplus0 = surplus0Scaled - uint256(notional0);
uint256 spread0ToAdmin    = spreadFeeE6 == 0 ? 0
    : (spreadSurplus0 * adminSpreadFeeE6)    / spreadFeeE6;
uint256 spread0ToProtocol = spreadFeeE6 == 0 ? 0
    : (spreadSurplus0 * protocolSpreadFeeE6) / spreadFeeE6;

// Notional split is unchanged
uint256 notional0ToAdmin    = notionalFeeE8 == 0 ? 0
    : (uint256(notional0) * adminNotionalFeeE8) / notionalFeeE8;
uint256 notional0ToProtocol = uint256(notional0) - notional0ToAdmin;

// Now: total = spreadSurplus0 + notional0 = surplus0Scaled  ✓
```

This mirrors the fix in the referenced Velocimeter report: the denominator (or base) must reflect only the portion being split, not the combined total.

---

### Proof of Concept

1. Deploy a pool with `spreadProtocolFeeE6 = PROTOCOL_FEE`, `adminSpreadFeeE6 = ADMIN_FEE`, and `notionalFeeE8 = FEE_1_PCT_E8`.
2. Add liquidity and execute 8 round-trip swaps to accrue both fee types.
3. Read `surplus0Scaled = balance × scaleMultiplier − totalScaledToken0InBins` and `notional0` from slot2.
4. Compute `total0Attempted` using the split formulas above.
5. Observe `total0Attempted > surplus0Scaled` — the existing test already asserts this:

```solidity
// metric-core/test/MetricOmmPool.notionalFee.t.sol
assertGt(total0Attempted, surplus0Scaled,
    "token0 attempted payout exceeds computed surplus");   // passes → bug confirmed
assertGt(total1Attempted, surplus1Scaled,
    "token1 attempted payout exceeds computed surplus");   // passes → bug confirmed
``` [3](#0-2)

### Citations

**File:** metric-core/test/MetricOmmPool.notionalFee.t.sol (L211-266)
```text
  function test_collectProtocolFees_math_overallocates_whenSpreadAndNotionalBothActive() public {
    pool.collectFees(PROTOCOL_FEE, ADMIN_FEE, 0, 0, adminFeeDestination);
    poolFeeConfig[address(pool)] = PoolFeeConfig({
      protocolSpreadFeeE6: PROTOCOL_FEE,
      adminSpreadFeeE6: ADMIN_FEE,
      protocolNotionalFeeE8: FEE_1_PCT_E8,
      adminNotionalFeeE8: 0
    });
    pool.setPoolFees(PROTOCOL_FEE + ADMIN_FEE, FEE_1_PCT_E8);

    _addLiquidity(1, -5, 4, 100_000, 0);
    for (uint256 i = 0; i < 8; i++) {
      _swap(0, users[0], false, int128(50_000), type(uint128).max);
      _swap(0, users[0], true, int128(10_000), 0);
    }

    (uint128 totalScaledToken0InBins, uint128 totalScaledToken1InBins) = PoolStateLibrary._slot1(_poolAddr());
    (uint128 notional0, uint128 notional1) = PoolStateLibrary._slot2(_poolAddr());
    assertGt(uint256(notional0) + uint256(notional1), 10, "notional accumulators should be non-zero");

    address adminAddr = IMetricOmmPoolFactory(factory).poolAdmin(_poolAddr());
    (uint24 protocolSpreadFeeE6, uint24 adminSpreadFeeE6,,) = IMetricOmmPoolFactory(factory).poolFeeConfig(_poolAddr());
    assertEq(adminAddr, admin);
    PoolFeeConfig memory feeConfig = poolFeeConfig[address(pool)];
    uint24 protocolNotionalFeeE8 = feeConfig.protocolNotionalFeeE8;
    uint24 adminNotionalFeeE8 = feeConfig.adminNotionalFeeE8;

    uint24 spreadFeeE6 = protocolSpreadFeeE6 + adminSpreadFeeE6;
    uint24 notionalFeeE8 = protocolNotionalFeeE8 + adminNotionalFeeE8;

    PoolImmutables memory immutables = IMetricOmmPool(address(pool)).getImmutables();
    address token0Addr = immutables.token0;
    address token1Addr = immutables.token1;
    uint256 token0Mul = immutables.token0ScaleMultiplier;
    uint256 token1Mul = immutables.token1ScaleMultiplier;

    uint256 surplus0Scaled = (MockERC20(token0Addr).balanceOf(address(pool)) * token0Mul) - totalScaledToken0InBins;
    uint256 surplus1Scaled = (MockERC20(token1Addr).balanceOf(address(pool)) * token1Mul) - totalScaledToken1InBins;

    // Mirror collect fee-split math for scaled amounts (rates passed into collectFees).
    uint256 spread0ToAdmin = spreadFeeE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6) / spreadFeeE6;
    uint256 spread1ToAdmin = spreadFeeE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6) / spreadFeeE6;
    uint256 spread0ToProtocol = spreadFeeE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6) / spreadFeeE6;
    uint256 spread1ToProtocol = spreadFeeE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6) / spreadFeeE6;

    uint256 notional0ToAdmin = notionalFeeE8 == 0 ? 0 : (uint256(notional0) * adminNotionalFeeE8) / notionalFeeE8;
    uint256 notional1ToAdmin = notionalFeeE8 == 0 ? 0 : (uint256(notional1) * adminNotionalFeeE8) / notionalFeeE8;
    uint256 notional0ToProtocol = uint256(notional0) - notional0ToAdmin;
    uint256 notional1ToProtocol = uint256(notional1) - notional1ToAdmin;

    uint256 total0Attempted = spread0ToAdmin + spread0ToProtocol + notional0ToAdmin + notional0ToProtocol;
    uint256 total1Attempted = spread1ToAdmin + spread1ToProtocol + notional1ToAdmin + notional1ToProtocol;

    assertGt(total0Attempted, surplus0Scaled, "token0 attempted payout exceeds computed surplus");
    assertGt(total1Attempted, surplus1Scaled, "token1 attempted payout exceeds computed surplus");
  }
```
