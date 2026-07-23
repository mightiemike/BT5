### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the pool to every user, completely defeating the per-user allowlist.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`), the router is the entity that calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

So `sender` arriving at the extension is the **router address**, not the end user. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

This creates an irreconcilable conflict for any pool admin who wants to:
1. Restrict swaps to a specific set of users (the purpose of the allowlist), **and**
2. Allow those users to use the router (for multi-hop, slippage protection, etc.).

To satisfy (2), the admin must call `setAllowedToSwap(pool, router, true)`. The moment they do, condition (1) is destroyed: every address on-chain can now call the router and pass the extension check, because the check resolves to `allowedSwapper[pool][router] == true` regardless of who the actual end user is.

The router stores the real payer in transient storage for callback settlement (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), so the non-allowlisted user's tokens are correctly pulled — the swap fully executes on their behalf.

---

### Impact Explanation

The swap allowlist is the sole on-chain mechanism for restricting who may trade against a pool. Once the router is allowlisted (a natural administrative step for any pool that wants to support router-mediated swaps), the allowlist provides zero protection: any address can route through `MetricOmmSimpleRouter` and execute swaps that the pool admin intended to block. This is a broken core pool functionality / admin-boundary break: an unprivileged actor bypasses a configured role check through a public periphery path.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router. This is a predictable and natural administrative action — any pool that wants to support the standard periphery router for its approved users must allowlist it. The admin has no way to simultaneously allow router-mediated swaps and enforce per-user identity checks with the current extension design, so the bypass is reachable in any production pool that uses both the allowlist and the router.

---

### Recommendation

The extension must gate the **end user's identity**, not the immediate caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Add a dedicated `originator` field to the swap call**: The pool interface exposes an explicit `originator` parameter (separate from `msg.sender`) that the router populates with the end user's address; the extension checks that field instead of `sender`.

Until one of these is implemented, the allowlist should document that it only gates direct pool callers and cannot be used to restrict end users who route through the periphery router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls: setAllowedToSwap(pool, alice, true)
  admin calls: setAllowedToSwap(pool, router, true)   ← needed so alice can use the router

Attack:
  mallory (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: mallory,
        tokenIn: token1,
        amountIn: X,
        ...
    })

  Router calls: pool.swap(mallory, false, X, ...)
    → msg.sender to pool = router
    → _beforeSwap(router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; mallory receives token0
    → router callback pulls token1 from mallory

Result: mallory swaps successfully despite never being allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) 
<cite repo="patrich

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
