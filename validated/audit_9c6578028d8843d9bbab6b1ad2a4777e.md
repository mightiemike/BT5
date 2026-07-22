### Title
Router-Mediated Swaps Bypass the `SwapAllowlistExtension` Per-User Gate — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the original user. The extension therefore checks whether the **router** is allowlisted, not the actual end-user. If the pool admin allowlists the router (a natural step to support router-mediated swaps for allowlisted users), every unprivileged user can bypass the per-user restriction by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← always the immediate caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` = pool):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls `pool.swap()` directly, making itself `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The original user's address is stored only in transient storage for the payment callback; it is **never forwarded** to the pool's `swap()` call as `sender`. The extension therefore sees `sender = router`, not `sender = user`.

---

### Impact Explanation

A pool admin who wants to allow router-mediated swaps for their allowlisted users must add the router to `allowedSwapper[pool]`. Once the router is allowlisted, **any unprivileged user** can call `router.exactInputSingle()` (or any other `exact*` function) and the extension will pass the check because `sender = router` is allowlisted. The per-user restriction is completely nullified.

Concrete consequence: a pool configured as a restricted institutional venue (e.g., only specific market-maker addresses may swap) is fully open to arbitrary swappers the moment the router is allowlisted. Those swappers can drain LP value at oracle-derived prices without any of the trust assumptions the pool admin intended to enforce.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production extension explicitly designed to restrict swap access.
- Pool admins who want allowlisted users to use the router (the standard periphery entry point) have no choice but to allowlist the router address.
- The router is a public, permissionless contract; any user can call it.
- No privileged access, no special token, no malicious setup is required — a standard `exactInputSingle` call suffices.

---

### Recommendation

The router must forward the original user's identity to the pool so the extension can gate the correct actor. Two complementary fixes:

1. **Router-side**: Add a `sender` parameter to each `exact*` function (defaulting to `msg.sender`) and pass it as the first argument to `pool.swap()` via a dedicated field or by encoding it in `callbackData` and having the pool expose it. Alternatively, the router can pass `msg.sender` as the `recipient`-adjacent identity through a new pool entry point.

2. **Extension-side**: `SwapAllowlistExtension` should accept an optional `extensionData` payload carrying the original user address (signed or verified by the router), and gate on that address rather than the raw `sender` when the immediate caller is a known router.

Until fixed, pool admins should **not** allowlist the router address; allowlisted users must call `pool.swap()` directly (implementing `IMetricOmmSwapCallback` themselves).

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   (alice is the intended grantee)
  allowedSwapper[pool][router]  = true   (admin adds router so alice can use it)

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes, charlie receives output tokens

Result: charlie, who is not allowlisted, successfully swaps on a restricted pool
        by routing through the public MetricOmmSimpleRouter.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
