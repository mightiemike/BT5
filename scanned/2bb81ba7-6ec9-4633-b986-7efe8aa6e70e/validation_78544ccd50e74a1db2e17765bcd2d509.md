### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router address (a natural step when trying to enable router-based swaps for permitted users), every unpermissioned user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

**Root cause — wrong actor checked in the hook:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and uses `msg.sender` (the pool) as the pool key:

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

The pool always passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

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

So `msg.sender` to the pool is the router contract. The extension then evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The structural trap:** A pool admin who wants to support router-based swaps for their allowlisted users must allowlist the router address. Doing so opens the pool to every user of the public router, defeating the per-user gate entirely. There is no way to allowlist specific users for router-mediated swaps with the current design.

**Analog to the DeleteAll bug:** Just as `DeleteAll` only iterated `dirtyItems` and silently missed all other keys in the store, `SwapAllowlistExtension` only checks the direct caller of `pool.swap` and silently misses the actual end user when an intermediary (the router) is in the call stack.

---

### Impact Explanation

Any user can bypass a curated pool's swap allowlist by routing through the public `MetricOmmSimpleRouter`. If the pool admin has allowlisted the router (to enable router-based swaps for their permitted users), every unpermissioned address can execute swaps against the pool. This breaks the core access-control invariant of the `SwapAllowlistExtension` and constitutes an admin-boundary break: an unprivileged path circumvents the policy the pool admin intended to enforce.

---

### Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address. This is a natural and expected action: a pool admin who wants their allowlisted users to be able to use the standard periphery router will add the router to the allowlist. The mistake is non-obvious because the admin may believe they are enabling router access for their specific users, not for all users. The router is a public, immutable, permissionless contract, so once it is allowlisted, the bypass is available to anyone.

---

### Recommendation

The extension should check the actual end user, not the intermediary. Two approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Check `sender` only for direct pool calls; require the router to forward the real user identity:** Add a trusted-forwarder pattern where the router encodes the real user and the extension verifies the forwarder's identity before trusting the encoded address.

At minimum, document clearly that allowlisting the router address opens the pool to all router users, and that per-user gating of router-mediated swaps is not supported by the current extension design.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  pool admin calls: swapExtension.setAllowedToSwap(pool, alice, true)
  pool admin calls: swapExtension.setAllowedToSwap(pool, router, true)
    ↑ admin intends to let alice use the router; unknowingly opens pool to all

Attack:
  charlie (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

  Router calls:
    pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender = router

  Pool calls:
    _beforeSwap(router, recipient, ...)

  Extension evaluates:
    allowedSwapper[pool][router] → true   ← charlie bypasses the gate

  Result: charlie's swap executes successfully against the curated pool
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
