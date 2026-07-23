### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of the pool is the **router contract**, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, the allowlist is silently opened to **every user** who routes through the same public router, because the extension cannot distinguish between different end users behind the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`:**

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

`msg.sender` here is the pool (the pool calls the extension). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of `MetricOmmPool.swap()`.

**How the pool binds `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

**How the router calls the pool:**

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

The router calls `pool.swap()` directly. Therefore `msg.sender` of the pool = **router address**, and `sender` forwarded to the extension = **router address**, not the end user.

**The bypass:**

A pool admin who wants to allow KYC'd users to swap via the router must allowlist the router address (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, the extension check passes for **any** caller of the router, because the extension only sees `sender = router`. A non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the curated pool; the extension sees `sender = router` (allowlisted) and permits the swap.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → legitimate allowlisted users cannot use the standard periphery.
- **Allowlist the router** → the allowlist is bypassed for all users.

---

### Impact Explanation

A curated pool's swap allowlist is completely bypassed. Any unprivileged user can execute swaps on a pool that the admin intended to restrict to specific participants (e.g., KYC'd counterparties, whitelisted market makers). This allows unauthorized users to consume pool liquidity at oracle-derived prices, directly extracting value from LP positions that were deposited under the assumption of a restricted trading environment. This is a direct loss of LP principal and a broken core pool functionality (curation).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps (the normal user flow) will encounter this issue. The attacker needs no special privileges — only the ability to call the public router. The bypass is reachable on every curated pool that allowlists the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original transaction initiator**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original user through the router**: The router should forward the original `msg.sender` as a verified parameter (e.g., via `extensionData`), and the extension should verify it against the allowlist. This requires a trusted forwarding convention.

2. **Check `tx.origin` as a fallback**: Only acceptable if the pool admin explicitly opts in and understands the implications.

3. **Require direct pool calls for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router; users must call the pool directly. This breaks the standard UX but preserves the invariant.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData` and the extension verifies it, with the pool's `onlyPool` guard ensuring the data was not forged by an external caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin allowlists alice (KYC'd user): allowedSwapper[pool][alice] = true
  - Pool admin allowlists router: allowedSwapper[pool][router] = true
    (necessary so alice can use the standard periphery)

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
        pool: curated_pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(bob, true, X, ...) → msg.sender of pool = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true → PASSES
  - Swap executes; bob receives tokens from the curated pool
  - Allowlist invariant broken: bob was never authorized to swap
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
