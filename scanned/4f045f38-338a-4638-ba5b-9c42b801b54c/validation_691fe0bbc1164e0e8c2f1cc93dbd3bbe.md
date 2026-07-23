### Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. The extension therefore checks the router's address against the allowlist, not the real swapper's address. Any user can bypass a curated pool's per-user allowlist by calling the router.

---

### Finding Description

**Root cause — wrong actor bound to the allowlist check**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

**Router path — the router is always `msg.sender` of the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

`exactInput` (multi-hop) also calls `pool.swap` from the router for every hop: [5](#0-4) 

In both cases the pool's `msg.sender` is the router, so `sender` delivered to the extension is the router address, not the real user.

**Contrast with `DepositAllowlistExtension`**

The deposit-side extension correctly ignores `sender` (the first, unnamed parameter) and checks `owner` — the position owner explicitly passed by the pool: [6](#0-5) 

No equivalent correction exists on the swap side.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Every router-mediated swap reverts — allowlisted users cannot use the standard periphery |
| Router **allowlisted** | Every user, including non-allowlisted ones, can swap through the router — the per-user gate is fully bypassed |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The allowlist invariant is broken for all router-mediated swaps, which is the primary swap entry point for end users. This constitutes broken core pool functionality and an admin-boundary bypass reachable by any unprivileged caller.

---

### Likelihood Explanation

**High.** The router is the standard, documented swap entry point. Any user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` without any special privilege. No frontrunning, flash loan, or multi-block setup is required — a single transaction through the router suffices.

---

### Recommendation

The extension must check the identity of the economic actor, not the intermediary. Two viable approaches:

1. **Pass the real user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is the user-controlled address. This is imperfect for multi-hop paths where intermediate recipients are the router itself.

3. **Apply the allowlist at the router level**: Gate `exactInputSingle` / `exactInput` in the router before calling the pool, and remove the extension-level check. This is the cleanest separation of concerns.

The asymmetry with `DepositAllowlistExtension` (which correctly checks `owner`) should be used as the model: identify the economically relevant actor and check that address, not the calling contract.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed user
  allowedSwapper[pool][router] = true  // admin must allowlist router for router swaps to work

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: T0, tokenOut: T1, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)   // msg.sender of pool = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes for bob

Result: bob swaps successfully on a pool that should have blocked him.
```

If the admin does NOT allowlist the router, alice (the intended user) cannot swap through the router either — the extension reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router] == false`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
