### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool binds to `msg.sender` at the pool call boundary. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the **router contract**, not the end user. The allowlist therefore gates the router's address, not the economic actor. A pool admin who allowlists the router to enable standard periphery access inadvertently opens the pool to every user, completely defeating the per-user curation the extension was deployed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of the pool
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against its per-pool mapping:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
```

At that point `msg.sender` inside the pool is the **router**, so `sender = router`. The allowlist evaluates `allowedSwapper[pool][router]`, never touching the end user's address. The actual user (`msg.sender` in the router) is invisible to the guard.

---

### Impact Explanation

A pool admin who intends to restrict swaps to a curated set of addresses faces an inescapable dilemma:

| Admin choice | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot swap through the standard periphery at all — broken core functionality |
| **Allowlist the router** | Every user on-chain can bypass the per-user allowlist by routing through the router — full allowlist bypass |

In the second (and operationally necessary) case, any non-allowlisted user executes swaps on a pool that was designed to be curated. This constitutes a direct loss of the curation guarantee, enables unauthorized trading against LP positions, and can drain LP principal through trades the pool admin explicitly intended to block.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery swap interface. No special privilege or setup is required — any EOA or contract can call `exactInputSingle`. The bypass is reachable on every curated pool the moment the admin allowlists the router (a step they must take to give allowlisted users access to the standard UX). The trigger is a normal, unprivileged swap call.

---

### Recommendation

Bind the allowlist check to the **end user**, not the direct pool caller. The cleanest fix is to pass the original user through the extension payload or to have the router forward the originating address explicitly. Alternatively, `SwapAllowlistExtension.beforeSwap` should check `sender` only when `sender` is not a known router, and fall back to an `extensionData`-encoded user address when the direct caller is a trusted router. The simplest invariant-preserving fix: the pool should pass the **payer / originating user** as `sender`, not `msg.sender`, or the extension must be documented as incompatible with router-mediated flows and a router-aware variant must be provided.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the standard router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` → `msg.sender` in pool = router.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **true**.
7. Bob's swap executes successfully despite never being allowlisted.

The guard that was supposed to block Bob reads the router's allowlist entry instead of Bob's, and the router is allowlisted, so the check passes. The invariant "only allowlisted addresses may swap" is broken for every router-mediated call. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
