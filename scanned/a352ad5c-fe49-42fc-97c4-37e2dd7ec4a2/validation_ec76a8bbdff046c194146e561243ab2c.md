### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any Caller to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. If the pool admin allowlists the router address (the only way to let their users access the pool via the router), every unpermissioned user can bypass the allowlist by calling the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as follows:

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which passes `msg.sender` of the pool's `swap()` call:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient, zeroForOne, ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

At this point `msg.sender` inside `pool.swap()` is the **router address**, not the user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable conflict for any pool admin who wants to:
1. Restrict swaps to a specific set of users (e.g., KYC-gated pool), **and**
2. Allow those users to use the public `MetricOmmSimpleRouter`.

To satisfy (2), the admin must add the router to the allowlist. Once the router is allowlisted, condition (1) is completely defeated: any unpermissioned user can call `router.exactInputSingle()` and the extension will pass because `allowedSwapper[pool][router] == true`. There is no mechanism in the router to restrict which users it forwards, and the extension receives no information about the actual caller behind the router.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to enforce a restricted-access policy (e.g., KYC, whitelist-only trading) is rendered completely open to any user the moment the pool admin allowlists the router. The allowlist guard is misapplied: it gates the router contract address rather than the economically relevant actor (the end user). Any non-allowlisted user can execute swaps against the pool, draining LP value at oracle-derived prices without the intended access control.

This is a direct loss-of-policy impact: the pool's curated access boundary is silently broken, and LP funds are exposed to unrestricted trading that the pool admin explicitly intended to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy a restricted pool and want their allowlisted users to have a normal UX will naturally allowlist the router. The router is a public, permissionless contract with no user-level access controls. The misconfiguration is not obvious from the extension's interface or documentation, and no existing test covers the router-mediated bypass path on an allowlisted pool.

---

### Recommendation

The `beforeSwap` hook should receive the **original user** identity, not the intermediary caller. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Add a `realSender` field to the hook signature**: The pool passes both `msg.sender` (the immediate caller) and an optional `realSender` that the router populates via a transient-storage slot before calling `pool.swap()`. The extension checks `realSender` when non-zero, falling back to `sender` for direct calls.

Either approach ensures the allowlist gates the economically relevant actor regardless of which periphery path is used.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin allowlists alice (KYC'd user) but NOT bob.
  - Pool admin also allowlists the router so alice can use it.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(); msg.sender = router.
  3. _beforeSwap(sender=router, ...) is dispatched.
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  5. bob's swap executes at oracle price; allowlist is bypassed.

Result: bob, a non-allowlisted user, successfully swaps on a pool that was
        intended to be restricted to alice only.
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
