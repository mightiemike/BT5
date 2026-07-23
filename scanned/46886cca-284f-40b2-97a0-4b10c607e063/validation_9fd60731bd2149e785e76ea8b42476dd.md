### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router address to enable router-mediated swaps, every unprivileged user can bypass the per-pool allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]` — the router's address — rather than the original EOA. A pool admin who wants to permit router-mediated swaps must allowlist the router address. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of whether that caller is individually permitted.

---

### Impact Explanation

Any user who is not individually allowlisted can bypass the curated-pool access control by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) on a pool whose admin has allowlisted the router. The swap executes at the oracle-derived price, draining LP assets to an unauthorized counterparty. Because the router is a public, permissionless contract, no special privilege is required beyond holding the input token.

---

### Likelihood Explanation

The pool admin must allowlist the router to allow any router-mediated swap. This is a natural and expected configuration step: the router is the canonical periphery entry point, and an admin who wants users to be able to use it must add it to the allowlist. The admin has no way to simultaneously allow the router and restrict individual users through the same `SwapAllowlistExtension` mechanism, because the extension only sees the router's address. The configuration that triggers the bypass is therefore the normal, intended one for router-enabled curated pools.

---

### Recommendation

Pass the original user's identity through the swap path so the extension can gate on the economically relevant actor. Two concrete options:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it when present.
2. **Extension-side**: Change `SwapAllowlistExtension` to check `sender` only when `sender` is not a known router, or add a separate `allowedRouter` mapping that the extension uses to look up the original user from `extensionData`.

The `DepositAllowlistExtension` avoids this problem by checking `owner` (the position beneficiary) rather than `sender` (the payer/operator): [5](#0-4) 

The swap extension should adopt an analogous design that separates the economic actor from the transaction originator.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin = pool admin
  alice = allowlisted user (allowedSwapper[pool][alice] = true)
  bob   = non-allowlisted user
  router = MetricOmmSimpleRouter (public, permissionless)

Step 1 – Admin allowlists the router so alice can use it:
  admin calls swapExtension.setAllowedToSwap(pool, router, true)
  → allowedSwapper[pool][router] = true

Step 2 – Bob (not allowlisted) calls the router:
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
    msg.sender of pool.swap = router

Step 3 – Pool dispatches beforeSwap:
  pool calls _beforeSwap(router, bob, ...)
  → extension receives sender = router, msg.sender = pool
  → checks allowedSwapper[pool][router] → true  ✓
  → swap proceeds

Result: Bob, who is not individually allowlisted, executes a swap on the
curated pool. The allowlist is fully bypassed.

Direct call by bob (without router) would fail:
  bob calls pool.swap(bob, ...)
  → extension checks allowedSwapper[pool][bob] → false → NotAllowedToSwap ✗
```

The bypass is reachable through the supported public periphery path (`MetricOmmSimpleRouter`) with no special privilege, and the root cause is the wrong-actor binding in `SwapAllowlistExtension.beforeSwap`. [6](#0-5) [7](#0-6)

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
