### Title
Mutable `notionalFeeE8` / `spreadFeeE6` Lack Hardcoded Caps, Enabling Pool-Admin Frontrun of Swaps — (File: metric-core/contracts/MetricOmmPool.sol)

---

### Summary

`MetricOmmPool` stores two mutable fee parameters — `notionalFeeE8` and `spreadFeeE6` — that are read at swap-execution time. The factory-side pool admin can call `setPoolFees()` with no hardcoded upper bound enforced at the pool level. This allows the admin to atomically raise fees before a pending swap lands, extract excess value from the trader, and reset fees — a direct structural analog to the M-03 TurboSafe.slurp frontrun.

---

### Finding Description

`notionalFeeE8` and `spreadFeeE6` are declared as mutable `uint24` state variables: [1](#0-0) 

They are consumed live inside `_executeSwap()` at the moment the swap runs: [2](#0-1) 

`spreadFeeE6` is also forwarded into every per-bin swap helper (e.g., `SwapMath.buyToken0InBinSpecifiedOut`) that computes how much of the input the LP retains: [3](#0-2) 

Both values are updated by `setPoolFees()`, which is gated only by `onlyFactory` and contains **no cap check**: [4](#0-3) 

`uint24` max is 16,777,215. For `notionalFeeE8` (denominator 1e8) that is ≈16.8 %. For `spreadFeeE6` (denominator 1e6) that is ≈1,678 % — a value that would make the spread-fee multiplier overflow the intended price arithmetic and could drain the entire input from a trader.

The `_beforeSwap` / `_afterSwap` extension hooks receive the oracle bid/ask prices captured **before** the fee change takes effect, but the actual fee deduction happens inside `_executeSwap()` which reads the **post-change** storage values. Extensions therefore cannot observe or block the fee manipulation: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A malicious or compromised pool admin can:

1. Raise `spreadFeeE6` to `type(uint24).max` (≈1,678 %) or `notionalFeeE8` to `type(uint24).max` (≈16.8 %) immediately before a targeted swap lands.
2. The swap executes: the trader pays the inflated fee; the excess accrues as LP surplus (spread fee) or as `notionalFeeToken{0,1}Scaled` (notional fee), both of which are later collectible by the admin via `collectFees()`.
3. Admin resets fees to normal.

For `spreadFeeE6` at extreme values the swap-math multiplier exceeds 1, meaning the pool retains more than 100 % of the input — direct loss of trader principal. For `notionalFeeE8` the loss is bounded at ≈16.8 % of output, still material.

**Impact class**: Direct loss of trader principal / protocol-fee extraction above Sherlock thresholds; Admin-boundary break (pool admin exceeds implicit fee cap).

---

### Likelihood Explanation

The trigger is the factory `poolAdmin` role — a semi-trusted actor equivalent to the "clerk" in M-03. No timelock is enforced at the pool contract level. The admin can execute the three-step attack (raise → victim swap → reset) within a single block or across consecutive blocks using standard MEV infrastructure. No special token behavior or off-chain data is required.

---

### Recommendation

Add hardcoded maximum constants at the pool level and enforce them inside `setPoolFees()` and `setBinAdditionalFees()`:

```solidity
uint24 public constant MAX_SPREAD_FEE_E6   = 100_000;  // 10 %
uint24 public constant MAX_NOTIONAL_FEE_E8 =  10_000_000; // 10 %

function setPoolFees(uint24 newSpreadFeeE6, uint24 newNotionalFeeE8) external onlyFactory {
    require(newSpreadFeeE6   <= MAX_SPREAD_FEE_E6,   FeeTooHigh());
    require(newNotionalFeeE8 <= MAX_NOTIONAL_FEE_E8, FeeTooHigh());
    ...
}
```

This mirrors the judge's recommendation in M-03: a `MAX_FEE` hardcoded variable ensures fees can never reach a level that harms traders regardless of admin intent.

---

### Proof of Concept

```
Block N:
  Trader broadcasts: pool.swap(recipient, true, 1_000e18, 0, callbackData, "")

Block N (higher gas, same block):
  Admin broadcasts:
    factory.setPoolFees(poolAddr, type(uint24).max, type(uint24).max)
      → pool.spreadFeeE6  = 16_777_215  (~1678%)
      → pool.notionalFeeE8 = 16_777_215  (~16.8%)

  Trader's swap executes:
    _executeSwap reads notionalFeeE8 = 16_777_215
    notionalFeeScaled = outputAmount * 16_777_215 / 1e8
                      ≈ 16.8% of output withheld as notionalFeeToken1Scaled
    spreadFeeE6 passed to SwapMath → per-bin fee multiplier >> 1
    → trader receives far less token1 than expected

  Admin broadcasts:
    factory.setPoolFees(poolAddr, normalSpread, normalNotional)
      → fees reset

  Admin later calls:
    factory.collectFees(poolAddr, ...) → extracts accumulated notional fees
``` [4](#0-3) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L76-77)
```text
  uint24 internal spreadFeeE6;
  uint24 internal notionalFeeE8;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L227-248)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );

    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L364-434)
```text
  /// @inheritdoc IMetricOmmPoolCollectFees
  function collectFees(
    uint256 protocolSpreadFeeE6_,
    uint256 adminSpreadFeeE6_,
    uint256 protocolNotionalFeeE8_,
    uint256 adminNotionalFeeE8_,
    address adminFeeDestination_
  ) external onlyFactory nonReentrant(PoolActions.COLLECT_FEES) {
    uint256 spreadSumE6;
    uint256 notionalSumE8;
    unchecked {
      spreadSumE6 = protocolSpreadFeeE6_ + adminSpreadFeeE6_;
      notionalSumE8 = protocolNotionalFeeE8_ + adminNotionalFeeE8_;
      if (spreadSumE6 == 0 && notionalSumE8 == 0) {
        return;
      }
    }

    uint256 notionalFee0AmountScaled = notionalFeeToken0Scaled;
    uint256 notionalFee1AmountScaled = notionalFeeToken1Scaled;

    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;

    unchecked {
      uint256 spreadFee0ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6_) / spreadSumE6;

      uint256 spreadFee0ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6_) / spreadSumE6;

      uint256 notionalFee0ToAdminScaled =
        notionalSumE8 == 0 ? 0 : (notionalFee0AmountScaled * adminNotionalFeeE8_) / notionalSumE8;
      uint256 notionalFee1ToAdminScaled =
        notionalSumE8 == 0 ? 0 : (notionalFee1AmountScaled * adminNotionalFeeE8_) / notionalSumE8;

      uint256 notionalFee0ToProtocolScaled = notionalFee0AmountScaled - notionalFee0ToAdminScaled;
      uint256 notionalFee1ToProtocolScaled = notionalFee1AmountScaled - notionalFee1ToAdminScaled;

      uint256 totalFee0ToAdminScaled = spreadFee0ToAdminScaled + notionalFee0ToAdminScaled;
      uint256 totalFee1ToAdminScaled = spreadFee1ToAdminScaled + notionalFee1ToAdminScaled;

      uint256 totalFee0ToProtocolScaled = spreadFee0ToProtocolScaled + notionalFee0ToProtocolScaled;
      uint256 totalFee1ToProtocolScaled = spreadFee1ToProtocolScaled + notionalFee1ToProtocolScaled;

      (uint256 totalFee0ToAdmin, uint256 totalFee1ToAdmin) =
        deltasScaledToExternal(totalFee0ToAdminScaled, totalFee1ToAdminScaled, Math.Rounding.Floor);
      (uint256 totalFee0ToProtocol, uint256 totalFee1ToProtocol) =
        deltasScaledToExternal(totalFee0ToProtocolScaled, totalFee1ToProtocolScaled, Math.Rounding.Floor);

      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
      if (totalFee0ToProtocol > 0) {
        transferToken0(FACTORY, totalFee0ToProtocol);
      }
      if (totalFee1ToProtocol > 0) {
        transferToken1(FACTORY, totalFee1ToProtocol);
      }

      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;

      emit ProtocolFeesCollected(totalFee0ToProtocol, totalFee1ToProtocol, totalFee0ToAdmin, totalFee1ToAdmin);
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L437-452)
```text
  function setPoolFees(uint24 newSpreadFeeE6, uint24 newNotionalFeeE8)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_POOL_FEES)
  {
    unchecked {
      if (newSpreadFeeE6 != spreadFeeE6) {
        spreadFeeE6 = newSpreadFeeE6;
        emit SpreadFeeUpdated(newSpreadFeeE6);
      }
      if (newNotionalFeeE8 != notionalFeeE8) {
        notionalFeeE8 = newNotionalFeeE8;
        emit NotionalFeeUpdated(newNotionalFeeE8);
      }
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L750-793)
```text
      if (notionalFeeE8 > 0) {
        if (amountSpecified > 0) {
          // exact in: notional fee on output token
          if (zeroForOne) {
            // safe because amount1DeltaScaled is bounded by uint128 total scaled token1 in bins.
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 notionalFeeScaled = uint256(-amount1DeltaScaled) * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount1DeltaScaled = amount1DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken1Scaled = (uint256(notionalFeeToken1Scaled) + notionalFeeScaled).toUint128();
            }
          } else {
            // safe because amount0DeltaScaled is bounded by uint128 total scaled token0 in bins.
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 notionalFeeScaled = uint256(-amount0DeltaScaled) * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount0DeltaScaled = amount0DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken0Scaled = (uint256(notionalFeeToken0Scaled) + notionalFeeScaled).toUint128();
            }
          }
        } else {
          // Exact-out: notional fee on pre-bin-fee input notional, added to input token.
          if (zeroForOne) {
            uint256 notionalFeeScaled = feeExclusiveInputScaled * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount0DeltaScaled = amount0DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken0Scaled = (uint256(notionalFeeToken0Scaled) + notionalFeeScaled).toUint128();
            }
          } else {
            uint256 notionalFeeScaled = feeExclusiveInputScaled * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount1DeltaScaled = amount1DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken1Scaled = (uint256(notionalFeeToken1Scaled) + notionalFeeScaled).toUint128();
            }
          }
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L906-915)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken0InBinSpecifiedOut(
            binState,
            curPosInBinCache,
            state,
            params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
            lowerPriceX64,
            upperPriceX64,
            params.priceLimitX64,
            spreadFeeE6
          );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
