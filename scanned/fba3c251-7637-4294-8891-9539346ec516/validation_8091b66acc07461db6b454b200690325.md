### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User Identity, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool always sets to its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router address (a natural action to support router-mediated swaps for curated users), every non-allowlisted user can bypass the guard by routing through the router.

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

`msg.sender` here is the pool (correct for pool-identity). `sender` is the value the pool passes as the first argument to `_beforeSwap`.

**What the pool passes as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- always the immediate caller of pool.swap()
    recipient,
    ...
);
```

**What the router passes as `msg.sender` when it calls the pool:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The router calls `pool.swap(...)` directly; the pool's `msg.sender` is therefore the **router contract address**, not the end user. Every router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) exhibits the same behavior.

**The structural mismatch:**

| Call path | `sender` seen by extension | Allowlist entry needed |
|---|---|---|
| User → pool directly | user address | `allowedSwapper[pool][user]` |
| User → router → pool | **router address** | `allowedSwapper[pool][router]` |

A pool admin who wants allowlisted users to also be able to use the router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[msg.sender][sender]` evaluates to `allowedSwapper[pool][router]` for **every** router-mediated swap, regardless of who the end user is. The guard fails open for all users.

---

### Impact Explanation

Any non-allowlisted user can bypass the `SwapAllowlistExtension` on a curated pool by calling any `MetricOmmSimpleRouter` swap function. The pool receives and settles the swap normally; the extension's `beforeSwap` hook passes because it sees the allowlisted router address, not the blocked end-user address. This is a direct, complete bypass of the curated-pool access control with fund-impacting consequences (non-curated users trade against LP capital that was deposited under the assumption of a restricted swapper set).

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to allowlist the router — a natural and expected configuration step for any curated pool whose allowlisted users are expected to interact through the standard periphery. The admin has no on-chain signal that doing so opens the gate to all users; the `setAllowedToSwap` setter accepts any address without warning.

---

### Recommendation

The `beforeSwap` hook must gate the **economic actor** (the end user), not the immediate caller. Two options:

1. **Pass originator through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Separate originator field in the hook interface**: Add an `originator` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates from a router-supplied field (e.g., a dedicated transient slot set by the router before calling `pool.swap`). The extension then checks `allowedSwapper[pool][originator]`.

Until fixed, pool admins must not allowlist the router address; allowlisted users must call `pool.swap` directly.

---

### Proof of Concept

```
Setup
─────
1. Pool deployed with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, user1, true)
   → user1 is the only allowlisted swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true)
   → intended to let user1 use the router; actually opens the gate.

Attack (user2, not allowlisted)
────────────────────────────────
4. user2 calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — pool's msg.sender = router
6. Pool calls _beforeSwap(router, ...)
7. ExtensionCalling encodes and calls extension.beforeSwap(router, ...)
8. Extension evaluates: allowedSwapper[pool][router] == true → passes
9. Swap executes; user2 receives output tokens from the curated pool.

Result: user2 bypasses the SwapAllowlistExtension entirely.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
