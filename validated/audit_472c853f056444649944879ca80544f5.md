I need to systematically trace the hook plumbing and guard logic across all four extension contracts and the core pool to find an analog to the inflation-attack bug class (a misconfigured or bypassable guard with fund-impacting consequences).

**Step 1 — Classify the external bug into a Metric OMM invariant**

The external bug: a guard parameter (decimals offset = 0) is set too low, allowing an attacker to inflate the share price and steal from subsequent depositors. The invariant broken is: *LP share accounting must prevent a first-depositor from inflating the per-share price to zero out subsequent depositors' shares.*

**Step 2 — Map to scoped files/functions**

Candidate analogs:
- `DepositAllowlistExtension.beforeAddLiquidity` — checks `owner`, not `sender`
- `OracleValueStopLossExtension.afterSwap` — missing `onlyPool` modifier
- `PriceVelocityGuardExtension.beforeSwap` — missing `onlyPool` modifier
- `simulateSwapAndRevert` — calls extensions with caller-supplied bid/ask

**Step 3 — Trace each candidate**

**DepositAllowlistExtension — `owner` vs `sender`**

`beforeAddLiquidity` ignores the `sender` parameter and checks only `allowedDepositor[msg.sender][owner]`: [1](#0-0) 

`addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`: [2](#0-1) 

A non-allowlisted `sender` can call `addLiquidity(owner=allowlisted_address)` and the check passes. However, the LP position is created for the allowlisted `owner`, and tokens are pulled from `sender` via callback. The non-allowlisted address cannot gain an LP position for itself — it would only be paying tokens to create a position for someone else. The NatDoc explicitly documents this as the intended operator pattern: [3](#0-2) 

No fund-loss bypass exists here.

**OracleValueStopLossExtension — missing `onlyPool`**

`afterSwap` uses `_requireInitialized(msg.sender)` instead of `onlyPool`: [4](#0-3) 

The comment's claim holds: `initialize` is `onlyFactory`, and the factory only calls it for pools it deploys via `MetricOmmPoolDeployer`. `MetricOmmPool` has no function to call arbitrary external contracts, so no attacker can make a real pool call `afterSwap` with crafted parameters. A direct call from an attacker sets `msg.sender` = attacker address, which is not initialized. No bypass.

**PriceVelocityGuardExtension — missing `onlyPool`**

`beforeSwap` uses `msg.sender` as the pool address and updates `priceVelocityState[msg.sender]`: [5](#0-4) 

A direct call from an attacker updates `priceVelocityState[attacker_

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L147-148)
```text
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L199-204)
```text
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L47-58)
```text
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```
