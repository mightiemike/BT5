### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router address (a natural action to let allowlisted users use the router), every unprivileged user can bypass the allowlist by routing through the same public router.

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ...) [msg.sender = router]
     → ExtensionCalling._beforeSwap(msg.sender=router, recipient, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

Inside `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct), and `sender` is the first argument forwarded by the pool — which is the pool's own `msg.sender`, i.e., the router address.

The pool passes `msg.sender` as `sender` to the extension:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    recipient,
    ...
);
```

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass scenario:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists only Alice and Bob.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so Alice and Bob can use the router.
3. Any unprivileged user (Charlie) calls `router.exactInputSingle(...)` targeting the curated pool.
4. The extension sees `sender = router`, which is allowlisted → swap succeeds.
5. Charlie bypasses the allowlist entirely.

The router does not forward the original `msg.sender` to the pool; it calls `pool.swap(recipient, ...)` directly, making itself the pool's `msg.sender`.

### Impact Explanation

A curated pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties is fully bypassed by any user routing through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps at oracle-derived prices against LP capital that was intended to be accessible only to vetted counterparties. This constitutes a direct loss of LP principal through unauthorized trades and broken core pool access-control functionality.

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address — a natural and expected action for any pool that wants its allowlisted users to be able to use the standard periphery. The pool admin has no indication from the extension's interface or documentation that allowlisting the router opens the pool to all users. The router is a public, permissionless contract. Any user who observes the router is allowlisted can immediately exploit this.

### Recommendation

The `SwapAllowlistExtension` should check the **end user** identity, not the immediate caller of `pool.swap`. Two approaches:

1. **Check `recipient` instead of `sender`** — but `recipient` is the output destination, not the economic actor.
2. **Require the router to forward the original user** — the pool's `swap()` interface would need a separate `swapper` parameter distinct from `msg.sender`, or the extension should read the user from a trusted forwarding mechanism.
3. **Most practical fix**: Document that the router must never be allowlisted, and instead require users to call the pool directly for allowlisted pools. Alternatively, add a `swapper` field to the `beforeSwap` hook that the pool populates from a trusted source (e.g., a transient-storage user context set by the router before calling the pool).

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: setAllowedToSwap(pool, alice, true)
  - Pool admin: setAllowedToSwap(pool, router, true)  ← to let alice use the router

Attack:
  - charlie (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: charlie, ...})
  - Router calls pool.swap(charlie, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for charlie despite charlie not being allowlisted
```

**Relevant code locations:** [1](#0-0) 

The extension receives `sender` = pool's `msg.sender` (the router), not the end user. [2](#0-1) 

The pool passes `msg.sender` (the router) as `sender` to the extension. [3](#0-2) 

The router calls `pool.swap(...)` directly, making itself the pool's `msg.sender`. [4](#0-3) 

`_beforeSwap` encodes `sender` (= pool's `msg.sender` = router) and dispatches it to the extension.

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
