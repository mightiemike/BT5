### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument forwarded by the pool, which is `msg.sender` to `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. If the pool admin adds the router to the allowlist (the natural step to enable router-based swaps for legitimate users), every unprivileged address can bypass the allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the first argument) is in the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the **router**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The `DepositAllowlistExtension` does **not** share this flaw: it gates on `owner` (the position owner explicitly passed by the caller), which the liquidity adder forwards correctly. The swap path has no equivalent owner-level parameter — the only identity available is `sender`, which collapses to the router. [6](#0-5) 

---

### Impact Explanation

Any pool that deploys `SwapAllowlistExtension` to enforce KYC, compliance, or participant restrictions loses that enforcement for every user who routes through `MetricOmmSimpleRouter`. If the pool admin adds the router to the allowlist (the only way to let legitimate users swap via the router), the allowlist becomes a no-op for the router path: every address on-chain can call `router.exactInputSingle` and the extension will approve the swap because the router itself is allowlisted. Unauthorized traders can execute swaps that should be blocked, draining pool liquidity at oracle-derived prices that were only intended for vetted counterparties.

---

### Likelihood Explanation

The scenario requires the pool admin to have added the router to the allowlist. This is the natural and expected configuration: without it, even allowlisted users cannot use the router. Any operator who reads the docs, sees that the router is the standard entry point, and adds it to the allowlist will unknowingly open the gate to all users. The router is a public, permissionless contract, so no privileged access is needed by the attacker.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the **end user**, not the intermediary. Two complementary approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention.
2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is the user; however, for multi-hop swaps the intermediate recipient is the router itself, so this is not universally correct either.
3. **Preferred**: Add a dedicated `originalSender` field to the extension interface (or a standardized prefix in `extensionData`) that the pool populates from a trusted transient context set by the router, analogous to how the router already stores the payer in transient storage.

Until fixed, pools that require strict participant gating should not rely on `SwapAllowlistExtension` for router-mediated swaps.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (BEFORE_SWAP_ORDER → extension slot 1)
  pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    → router is allowlisted so legitimate users can swap via router
  attacker = address not in allowlist

Attack:
  attacker calls router.exactInputSingle({
    pool:          pool,
    tokenIn:       token0,
    tokenOut:      token1,
    zeroForOne:    true,
    amountIn:      X,
    ...
    extensionData: ""
  })

  router → pool.swap(recipient, true, X, limit, "", "")
    msg.sender to pool = router

  pool._beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓  (no revert)

  swap executes; attacker receives token1 output
  allowlist guard was never applied to the attacker's address
``` [3](#0-2) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
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
