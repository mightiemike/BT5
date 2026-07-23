### Title
SwapAllowlistExtension Bypass via Router Intermediary — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the original user. A pool admin who allowlists the router (the only way to permit router-mediated swaps for allowlisted users) inadvertently opens the gate to every user on-chain, completely defeating the per-user allowlist.

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards this value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` forwarded to the extension is the router, not the original user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the router must call:

```solidity
swapExtension.setAllowedToSwap(pool, address(router), true);
```

Once the router is allowlisted, **any** address can call `router.exactInputSingle(...)` and the check passes unconditionally, because the extension only sees the router as `sender`.

The `DepositAllowlistExtension` has a related but distinct issue: it gates on `owner` (the LP position recipient), not on `sender` (the payer). Because `owner` is a free caller-supplied parameter in `pool.addLiquidity`, any non-allowlisted address can call `pool.addLiquidity(allowlistedAddress, ...)` and pass the check while funding the deposit themselves. [5](#0-4) [6](#0-5) 

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to a pool. Bypassing it means:

- Any unprivileged address can trade in a pool the admin intended to be restricted (e.g., KYC-gated, institutional-only, or regulatory-scoped pools).
- The pool admin's configured access boundary is silently nullified by a public periphery contract without any privileged action by the attacker.
- If the restricted pool carries favorable pricing, lower fees, or specific risk parameters, unauthorized traders can extract value from LP positions that were sized and priced under the assumption of a controlled counterparty set.

This is an admin-boundary break: the pool admin's allowlist is bypassed by an unprivileged path through a public router.

### Likelihood Explanation

The bypass is highly predictable:

1. A pool admin who deploys a pool with `SwapAllowlistExtension` and wants allowlisted users to access the protocol's own router **must** allowlist the router — there is no other mechanism.
2. The router is a canonical, publicly known contract; allowlisting it is the obvious configuration step.
3. No special timing, flash loan, or oracle condition is required. Any EOA or contract can call `router.exactInputSingle` at any time.

### Recommendation

1. **Pass the originating user through the extension data**: The router should encode `msg.sender` (the original user) into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` should decode and check that address instead of (or in addition to) `sender`.

2. **Alternatively, check both `sender` and the decoded original user**: The extension can require that either the immediate caller or the decoded originator is allowlisted, preventing the router-as-proxy bypass.

3. **Document the invariant explicitly**: Until the code is changed, the NatSpec on `SwapAllowlistExtension` and `MetricOmmSimpleRouter` must state that allowlisting the router grants unrestricted swap access to all router users.

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists only alice for direct swaps
ext.setAllowedToSwap(pool, alice, true);
// Pool admin also allowlists the router so alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) swaps through the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Succeeds: allowedSwapper[pool][router] == true, sender == router
// Bob has bypassed the per-user allowlist entirely
``` [3](#0-2) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
