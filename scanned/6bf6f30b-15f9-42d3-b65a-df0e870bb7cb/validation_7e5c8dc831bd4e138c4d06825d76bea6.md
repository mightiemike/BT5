Looking at the external report, the bug class is **wrong-actor binding on a privileged action**: a function that accepts an owner/position identifier as a caller-supplied parameter executes fund-moving logic without verifying that `msg.sender` is actually authorized over that position.

I traced the analog candidates in this codebase systematically.

**`removeLiquidity` — protected** [1](#0-0) 

`if (msg.sender != owner) revert NotPositionOwner();` is enforced before any state change. No bypass path exists here.

**`addLiquidity` operator pattern — intentional design** [2](#0-1) 

The pool explicitly documents: *"msg.sender pays but need not equal owner (operator pattern)."* [3](#0-2) 

**`DepositAllowlistExtension` — checks `owner`, ignores `sender`** [4](#0-3) 

The extension ignores the `sender` (first) parameter and checks `allowedDepositor[pool][owner]`. An unauthorized caller can pass an allowlisted address as `owner` and bypass the check — but the pool's own NatDoc explicitly specifies *"owner must pass allowlist"*, making this the intended behavior. The unauthorized caller is the payer and loses tokens; the allowlisted address gains a removable position. No protocol or LP principal is at risk.

**`SwapAllowlistExtension` — checks `sender` correctly** [5](#0-4) 

`sender` is `msg.sender` of the pool's `swap` call. When the router calls `swap`, `sender` = router. Allowlisting the router is a deliberate admin choice, not a bypass.

**`PriceVelocityGuardExtension` — state update before check, but safe** [6](#0-5) 

State is written before the velocity check, but a failed check reverts the entire transaction including the storage write. No persistent corruption.

**Extension calling plumbing — no ordering or payload mismatch** [7](#0-6) 

Extensions are called in immutable order from packed `uint256` order words set at construction. No runtime reordering or cross-pool bleed is possible.

**`MetricOmmPoolLiquidityAdder` probe flow — extension called during probe, but `view`** [8](#0-7) 

The `DepositAllowlistExtension.beforeAddLiquidity` is `view`, so the probe revert leaves no persistent side effects.

Every candidate either has an explicit guard, is documented as intentional design, or produces no fund-impacting consequence for

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-58)
```text
    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
```
