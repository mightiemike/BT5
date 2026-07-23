Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the end-user address, enabling allowlist bypass or blocking allowlisted users - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. This structural mismatch produces two fund-impacting outcomes: (A) if the router is allowlisted, every address on the internet can bypass the per-user swap gate; (B) if the router is not allowlisted, explicitly allowlisted users are silently blocked from using the standard periphery path.

## Finding Description

**Root cause — wrong actor checked:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original user's address — `msg.sender` of `pool.swap` is the router: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Call chain:**
```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, ...)          // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  // ← wrong actor
```

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly gates on `owner`, an explicit argument supplied by the caller, not `sender` (the caller itself): [6](#0-5) 

The swap path has no equivalent explicit "swapper identity" argument, so the extension has no way to recover the true end-user address from the data it receives.

## Impact Explanation

**Scenario A — Allowlist bypass (High):** A pool admin who wants allowlisted users to be able to use the router must add the router address to the allowlist. Once `allowedSwapper[pool][router] == true`, every call through the router passes the check regardless of who the end user is. Any non-allowlisted address can call `router.exactInputSingle(...)` and trade on a pool that was intended to be restricted to specific counterparties. This is a direct policy bypass on a pool whose entire purpose is to restrict who can swap — broken core pool functionality with direct fund-impact (trades execute that should be blocked).

**Scenario B — Broken core functionality (High):** A pool admin who allowlists specific user EOAs (not the router) will find that those users are silently blocked when they attempt to use the router. The extension sees `sender = router_address`, which is not in the allowlist, and reverts with `NotAllowedToSwap`. Allowlisted users must call `pool.swap` directly, bypassing the standard periphery path entirely — broken core pool functionality.

## Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` is affected. The router is the documented standard periphery path for swaps. The mismatch is structural and requires no special preconditions beyond normal usage of the two contracts together. No attacker capability beyond being a normal router caller is required for Scenario A once the router is allowlisted.

## Recommendation

The extension should gate on the economically relevant actor. Two options:

1. **Preferred — check `recipient` instead of `sender`:** The `recipient` is the address that receives output tokens and is the economically meaningful actor. It is passed through the router unchanged (`params.recipient`). The extension already receives `recipient` as its second argument (currently ignored with `address`). Change line 37 to check `allowedSwapper[msg.sender][recipient]`.

2. **Alternative — add a `swapper` field to `extensionData`:** The router encodes the original `msg.sender` into `extensionData`, and the extension decodes and checks it. This requires a coordinated change to the router and extension.

## Proof of Concept

**Setup:** Pool with `SwapAllowlistExtension` configured. Pool admin allowlists the router so allowlisted users can use the router.

```solidity
poolAdmin.setAllowedToSwap(pool, address(router), true);
// Intended: allow router-mediated swaps for specific users
// Actual effect: all users can now swap via the router
```

**Attack (Scenario A):**
1. `attacker` (not in allowlist) calls `router.exactInputSingle({pool: pool, ...})`.
2. Router calls `pool.swap(recipient, ...)` — `msg.sender = router`.
3. Pool calls `_beforeSwap(router, ...)`.
4. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router] == true` → passes.
5. Attacker executes a swap on a pool restricted to specific counterparties.

**Broken-functionality variant (Scenario B, no special setup needed):**
1. Pool admin allowlists `alice` (EOA): `setAllowedToSwap(pool, alice, true)`.
2. `alice` calls `router.exactInputSingle({pool: pool, ...})`.
3. Extension sees `sender = router_address`, not `alice`.
4. `allowedSwapper[pool][router] == false` → reverts `NotAllowedToSwap`.
5. Alice cannot use the router despite being explicitly allowlisted.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
