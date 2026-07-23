### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Making the Allowlist Bypassable Through the Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router (a natural action to let their curated users access the router), every unprivileged user can bypass the allowlist by routing through it.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

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

The pool passes `msg.sender` of its own `swap()` call as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

At the pool, `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user — including non-allowlisted ones — bypasses the allowlist |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The same path exists for `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool and execute swaps that the allowlist was designed to block. This is a direct policy bypass on a live pool with real LP assets.

---

### Likelihood Explanation

The router is the canonical user-facing entry point documented and promoted by the protocol. A pool admin who wants their allowlisted users to be able to trade via the router will naturally add the router to the allowlist. The bypass is then immediately available to every user with no further preconditions. The trigger is a single, reasonable admin action, not an exotic attack.

---

### Recommendation

The extension must gate the **original user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through the router**: The router should forward the original `msg.sender` in `extensionData` and the extension should decode and verify it. This requires a coordinated convention between the router and the extension.

2. **Check `sender` against a router-aware allowlist**: Extend the extension so that when `sender` is a recognized router, it decodes the original user from `extensionData` and checks that address instead.

Either way, the extension must be redesigned so that the identity it checks is the economically relevant actor — the user who initiated the trade — not the contract that relayed it.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only `alice`.
2. Admin also calls `setAllowedToSwap(pool, router, true)` so that `alice` can use the router.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
4. Router calls `pool.swap(...)` with `msg.sender = router`.
5. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true`.
6. Bob's swap executes on the restricted pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
