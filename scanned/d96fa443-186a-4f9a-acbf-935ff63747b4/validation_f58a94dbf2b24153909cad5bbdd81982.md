### Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user's address. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the gate to every user on-chain, defeating the purpose of the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the first parameter) against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The consequence is a binary outcome:

| Router allowlisted? | Result |
|---|---|
| No | Every router-mediated swap reverts, even for individually allowlisted users |
| Yes | Every user on-chain can swap through the router, bypassing per-user gating |

A pool admin who wants to support router-mediated swaps for a curated set of users has no correct option: they must either block the router entirely or open it to everyone.

---

### Impact Explanation

**High.** The allowlist is the sole access-control mechanism on curated pools. Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps against a pool that was intended to be restricted. This is a direct allowlist bypass with no additional preconditions beyond the router being allowlisted, which is the expected operational state for any pool that wants to support the standard periphery.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the canonical swap entry point described in the protocol documentation. Pool admins who deploy a curated pool and want users to be able to use the standard router will allowlist it. The mismatch between "allowlist the router" and "allow all users" is non-obvious and not documented as a constraint. Any curated pool that has the router allowlisted is immediately exploitable by any address.

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the immediate caller of the pool. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.

2. **Check `sender` only when it is not a known router; otherwise decode user from `extensionData`**: The extension can maintain a registry of trusted routers and fall back to an encoded user identity for those callers.

The simplest correct fix for the direct-call case is already correct; only the router-mediated path needs to be addressed.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is KYC'd)
  - allowedSwapper[pool][router] = true  (admin enables router support)
  - bob is NOT in allowedSwapper

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
     → msg.sender inside pool = router
  3. pool._beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for bob despite bob not being allowlisted

Result: bob bypasses the per-user allowlist and swaps on a curated pool.
``` [3](#0-2) [4](#0-3) [1](#0-0)

### Citations

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
