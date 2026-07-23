### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **actual trader** is allowlisted. If the router address is added to the allowlist (a natural admin action when the pool is meant to be accessible via the public router), every unprivileged user bypasses the access gate entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

At this point `msg.sender` inside the pool is the **router**, so `sender` delivered to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Concrete bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses.
2. Admin calls `setAllowedToSwap(pool, router, true)` — a natural step when the pool is meant to be publicly accessible through the official router.
3. Any unprivileged address calls `router.exactInputSingle(...)`. The extension sees `sender = router`, finds it allowlisted, and permits the swap.
4. The allowlist is completely ineffective for router-mediated swaps.

The analog to the external bug is exact: just as `safeIncreaseAllowance` applies the **wrong operation** (increment instead of set), `beforeSwap` checks the **wrong identity** (router instead of end user), causing the configured guard to be misapplied with the same class of fund-impacting consequence.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is typically deployed to restrict trading to known counterparties (e.g., KYC'd users, whitelisted market makers, or specific protocols). Once the router is allowlisted, the restriction is void for all router-mediated swaps. Any address can execute swaps against the pool's LP positions at the oracle-derived bid/ask, draining LP value without the access control the pool admin intended to enforce. This constitutes a broken core pool invariant (the allowlist guard) with direct loss of LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public entry point for swaps. A pool admin who wants end users to trade through the router will naturally allowlist the router address. The bypass is then reachable by any unprivileged caller with zero additional preconditions. No special tokens, no malicious setup, and no privileged role is required beyond the admin's own legitimate configuration step.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the **transport layer** (the direct caller of `pool.swap()`). Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` in the pool, not in the extension**: The pool could expose a `swapFrom(address user, ...)` entry point that records the originating user in transient storage, and the extension reads that slot. This is the cleanest separation.

3. **Allowlist end users, not the router**: Document clearly that `sender` is always the direct caller of `pool.swap()`, so admins allowlist individual EOAs and never the router. Allowlisted EOAs must call the pool directly. This is a usage restriction, not a code fix, and breaks the router UX for restricted pools.

Option 1 or 2 is required for the guard to function correctly when the router is in the call path.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)
  alice (not individually allowlisted) wants to swap

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
  router calls pool.swap(recipient, ...)          // msg.sender = router
  pool calls _beforeSwap(router, ...)
  extension checks allowedSwapper[pool][router]   // true → no revert
  swap executes; alice receives output tokens

Expected:
  extension checks allowedSwapper[pool][alice]    // false → NotAllowedToSwap

Actual:
  alice's swap succeeds; allowlist is bypassed
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
