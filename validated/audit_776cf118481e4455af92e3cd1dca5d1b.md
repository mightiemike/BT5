### Title
SwapAllowlistExtension Gates on the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Configured Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` from the pool's perspective — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the direct caller, so `sender = router`. A pool admin who allowlists the router to enable router-mediated swaps for curated users simultaneously opens the allowlist to every user who routes through the same router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` inside `MetricOmmPool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

So `msg.sender` inside `pool.swap()` is the **router**, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — including non-allowlisted ones — can bypass the guard by routing through the router |

The same problem applies to all router entry points: `exactInput`, `exactOutputSingle`, and `exactOutput` (including the recursive callback hops in `_exactOutputIterateCallback`).

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for curation (e.g., KYC-only trading, institutional-only pools, or regulatory compliance) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user can execute swaps against the pool's LP liquidity, extracting value from LPs who expected a controlled trading environment. This is a direct loss of the access-control guarantee that the extension is designed to provide, with fund-impacting consequences for LP positions in curated pools.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point in `metric-periphery`. Any pool that deploys `SwapAllowlistExtension` and also needs to support router-mediated swaps (the normal production path) must allowlist the router, at which point the bypass is unconditionally available to every user. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the end user — not the intermediate router. Two approaches:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` forward `msg.sender` as a trusted field in `extensionData`, and have `SwapAllowlistExtension` decode and verify it (with a trusted-router registry to prevent spoofing).

2. **Check `recipient` instead of `sender` for router flows.** If the pool's design intent is to gate who receives output tokens, `recipient` is preserved correctly through the router (`params.recipient` is passed directly to `pool.swap()`). However, this changes the semantics of the allowlist.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension verifies the caller is a trusted router before trusting the decoded address.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only `alice` is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so `alice` can use it.
4. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob` successfully swaps against the curated pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
