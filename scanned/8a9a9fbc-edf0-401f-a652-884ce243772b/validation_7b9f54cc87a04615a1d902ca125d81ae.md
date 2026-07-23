### Title
`setPoolAdminFeeDestination` Redirects Already-Accrued Fees to New Destination Without Prior Settlement - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

### Summary
`MetricOmmPoolFactory.setPoolAdminFeeDestination` changes `poolAdminFeeDestination[pool]` without first calling `collectFees`. All fees that accrued under the old destination (both notional accumulator balances and spread surplus) are subsequently paid to the new destination when `collectPoolFees` is next called, permanently depriving the old destination of fees it was owed.

### Finding Description

The factory maintains a consistent pattern: every function that changes a fee-affecting parameter first flushes accrued fees at the current configuration before applying the new one.

`setPoolAdminFees` collects first: [1](#0-0) 

`setPoolProtocolFee` collects first: [2](#0-1) 

`setPoolAdminFeeDestination` does **not** collect first — it immediately overwrites the destination: [3](#0-2) 

When `collectPoolFees` (or any subsequent `collectFees` call) executes, it reads `poolAdminFeeDestination[pool]` at that moment and sends the entire admin share — including fees earned before the destination change — to the new address: [4](#0-3) 

Inside `collectFees` on the pool, the admin share is transferred to whatever `adminFeeDestination_` was passed in: [5](#0-4) 

The two fee components that are pending at the time of the destination change are:

1. **Notional fees** — explicitly tracked in `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled`: [6](#0-5) 

2. **Spread fees** — the surplus balance computed as `pool balance − binTotals − notionalFees`: [7](#0-6) 

Both are paid to the new destination after the change, not the old one.

### Impact Explanation

The old `adminFeeDestination` loses all fees that accrued during its tenure as the designated recipient. The magnitude equals the full admin share of every swap's spread surplus and notional accumulator that had not yet been collected at the time `setPoolAdminFeeDestination` was called. For an active pool with a non-trivial fee rate and collection interval, this can represent a material token amount. This is a direct loss of owed protocol/admin fees.

### Likelihood Explanation

`setPoolAdminFeeDestination` is a routine operational call — pool admins change treasury addresses during multisig rotations, DAO governance transitions, or contract upgrades. The pool admin is a semi-trusted role that legitimately calls this function. No adversarial setup is required; the loss occurs automatically on the next `collectPoolFees` call (which is itself permissionless and can be triggered by anyone). [8](#0-7) 

### Recommendation

Mirror the pattern used by `setPoolAdminFees` and `setPoolProtocolFee`: call `collectFees` with the **current** `poolAdminFeeDestination[pool]` before overwriting it.

```solidity
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();

    // Flush accrued fees to the OLD destination before changing it
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // old destination
    );

    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
}
```

### Proof of Concept

1. Pool is deployed with `adminFeeDestination = Alice`.
2. Multiple swaps occur; `notionalFeeToken0Scaled > 0` and spread surplus accumulates.
3. Pool admin calls `setPoolAdminFeeDestination(pool, Bob)`. No fees are collected; `poolAdminFeeDestination[pool]` is now `Bob`.
4. Anyone calls `collectPoolFees(pool)`. The factory reads `poolAdminFeeDestination[pool] == Bob` and passes it to `collectFees`.
5. Inside `collectFees`, the entire admin share (including fees earned while Alice was the destination) is transferred to `Bob`.
6. Alice receives nothing despite being the designated recipient when those fees were generated. [3](#0-2) [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L327-335)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L378-389)
```text
  /// @inheritdoc IMetricOmmPoolFactory
  function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-425)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L438-447)
```text
  function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L85-86)
```text
  uint128 internal notionalFeeToken0Scaled;
  uint128 internal notionalFeeToken1Scaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L382-432)
```text
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
```
