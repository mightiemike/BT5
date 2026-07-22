### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct caller of `MetricOmmPool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router becomes the direct caller of `pool.swap()`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Because the router is a public, permissionless contract, allowlisting it grants every user on the internet the ability to pass the check. The pool admin has no way to simultaneously enable router-mediated swaps and enforce per-user restrictions: either the router is allowlisted (allowlist is void) or it is not (router path is completely blocked).

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` as `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market participants) has its access control completely nullified for any user who routes through the public router. The admin-boundary the pool admin intended to enforce is bypassed by an unprivileged path. This matches the **admin-boundary break** and **broken core pool functionality** categories in the impact gate.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the canonical, documented user entry point for swaps. Any pool admin who deploys a swap-allowlisted pool and also wants normal users to interact through the router will allowlist the router address. This is the expected operational pattern; the bypass is therefore triggered by routine, correct administration.

---

### Recommendation

The extension must identify the actual end user, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of `sender`** — the recipient is the address that economically benefits from the swap output. The router always passes the user-supplied `params.recipient` directly to `pool.swap()`, so `allowedSwapper[pool][recipient]` would gate the economically relevant party regardless of which entry point is used.

2. **Require the router to forward the originating user via `extensionData`** — define a standard encoding that the router appends to `extensionData`, and have the extension decode and verify it. This requires the router to be updated to inject `msg.sender` into the extension payload before calling the pool.

Option 1 is simpler and requires no router changes; it mirrors how `DepositAllowlistExtension` correctly gates by `owner` (the position beneficiary) rather than `sender` (the direct caller): [6](#0-5) 

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice; Bob is **not** allowlisted.
4. Bob calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` — `msg.sender` inside the pool is the router.
6. Pool calls `_beforeSwap(router, bob, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was explicitly excluded from, receiving output tokens at the oracle price.

The allowlist is completely bypassed without any privileged action or non-standard token behavior.

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
