### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router address, not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for curated users), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Hook identity binding — `SwapAllowlistExtension.beforeSwap`**

The extension checks `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

**Pool passes `msg.sender` (the router) as `sender` to the extension**

```solidity
_beforeSwap(
  msg.sender,   // ← router address, not the actual user
  recipient,
  ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this unchanged:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
  sender,   // = router address
  ...
))
``` [3](#0-2) 

**Router never forwards the original user's address**

`MetricOmmSimpleRouter.exactInputSingle` stores the original user only in transient callback storage (for payment), and calls `pool.swap()` directly — so the pool's `msg.sender` is always the router:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

**The invariant break**

The pool admin cannot simultaneously:
1. Allow router-mediated swaps (by allowlisting the router address)
2. Enforce per-user allowlist restrictions

Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and every user — including non-allowlisted ones — passes the check by routing through the router. The extension never sees the actual user's address.

This is the direct analog to the external report's bug class: a protection mechanism that records/checks the wrong entity (the router instead of the actual user), leaving the guard incomplete and bypassable.

---

### Impact Explanation

A curated pool configured with `SwapAllowlistExtension` to restrict swaps to specific users (e.g., KYC-verified, institutional, or whitelisted counterparties) is completely bypassable by any unprivileged user routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool at oracle-anchored prices, draining LP assets and violating the pool's curation policy. This is a direct loss of LP principal and a broken core pool functionality (allowlist enforcement).

---

### Likelihood Explanation

The admin would naturally allowlist the router to allow allowlisted users to use the standard periphery — this is the expected and documented usage path. The admin's mental model is "I'm allowlisting a trusted intermediary," but the extension's design means "I'm allowlisting all users who go through that intermediary." No special privileges or setup are required for the exploit beyond the admin's reasonable configuration. Any user can then call `router.exactInputSingle()` targeting the curated pool and succeed.

---

### Recommendation

The extension must check the actual user identity, not the direct caller. Two approaches:

1. **`extensionData` forwarding**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. The pool already forwards `extensionData` unchanged to the extension.

2. **Documentation boundary**: Explicitly document that `SwapAllowlistExtension` cannot be used with `MetricOmmSimpleRouter` and that direct `pool.swap()` calls are required for allowlist enforcement to be meaningful.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin allowlists userA (KYC'd):
     extension.setAllowedToSwap(pool, userA, true)
3. Admin allowlists the router (to let userA use the standard periphery):
     extension.setAllowedToSwap(pool, router, true)
4. userB (NOT allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: userB, ...})
5. Router calls pool.swap() — msg.sender at pool = router.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks: allowedSwapper[pool][router] == true → passes.
8. userB's swap executes successfully, bypassing the allowlist entirely.
```

The extension checks `allowedSwapper[pool][router]` at step 7 instead of `allowedSwapper[pool][userB]`, so the per-user gate is never evaluated. The state that should have been checked — the actual user's allowlist entry — is never consulted, exactly mirroring the external report's pattern of an incomplete guard that omits the critical per-user update/check.

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
