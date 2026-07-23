### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual User — Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool always passes `msg.sender` (the immediate caller of `pool.swap`) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address against the allowlist — not the actual user's address. Any non-allowlisted user can bypass a curated pool's swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The pool sees `msg.sender = router`. It passes `router` as `sender` to the extension. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

Two broken outcomes follow:

1. **Allowlist bypass**: If the pool admin allowlists the router (e.g., to support router-mediated swaps for legitimate users), every address in the world can swap on the curated pool by routing through the public router. The per-user allowlist is completely nullified.

2. **Allowlisted users locked out of router**: If the admin does not allowlist the router, allowlisted users cannot use the router at all — they must call `pool.swap` directly, which requires them to implement `IMetricOmmSwapCallback` themselves.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, and to intermediate hops in multi-hop paths where the router calls `pool.swap` from inside `_exactOutputIterateCallback` with `msg.sender` (the pool, not the user) as the effective caller.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps, draining LP value at oracle-derived prices without the pool admin's consent. This is a direct loss of the access-control invariant the extension was designed to enforce, with fund-impacting consequences for LP principals on restricted pools.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user who is denied by the allowlist on a direct `pool.swap` call can immediately retry through the router with zero additional privilege. No special role, no admin action, and no unusual token behavior is required. The bypass is trivially reachable on every pool that uses `SwapAllowlistExtension` and has not allowlisted the router (or has allowlisted it to support legitimate router users).

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the immediate caller of `pool.swap`. Two complementary fixes:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` pass `msg.sender` (the user) as the `recipient`-equivalent "originator" field, and have the pool forward it as `sender` to extensions. Alternatively, encode the originator in `extensionData` and have the extension decode it — but this is forgeable unless the pool signs it.

2. **Preferred — check `tx.origin` or use a dedicated originator field**: The cleanest fix is for the pool's `swap` signature to accept an explicit `sender` address (the originator) that the router sets to `msg.sender` before calling the pool, and for the pool to pass that value — not `msg.sender` — to extensions. This mirrors how Uniswap v4 separates `sender` from `msg.sender` in hook calls.

Until fixed, pool admins should be warned that `SwapAllowlistExtension` provides no protection for pools accessible through `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin does NOT allowlist bob or the router

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient, ...)
     → pool.msg.sender = router
  3. pool calls _beforeSwap(router, ...)
  4. extension checks allowedSwapper[pool][router] → false
     → reverts NotAllowedToSwap

  Now admin allowlists router to let alice use the router:
  - Pool admin calls setAllowedToSwap(pool, router, true)

  5. bob calls router.exactInputSingle({pool: pool, ...})
  6. router calls pool.swap(recipient, ...)
     → pool.msg.sender = router
  7. pool calls _beforeSwap(router, ...)
  8. extension checks allowedSwapper[pool][router] → true  ✓ PASSES
     → bob's swap executes despite bob never being allowlisted
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
