### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User — Allowlist Fully Bypassed When Router Is Allowlisted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the direct caller, so the extension checks the **router's** allowlist status rather than the **end user's**. If the pool admin allowlists the router (a natural step to enable router-mediated swaps), every unprivileged user can bypass the per-user swap allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `sender` = router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Attack path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to specific counterparties.
2. Admin allowlists the router address so that allowlisted users can swap via the router (a natural operational step).
3. Any unprivileged user calls `router.exactInputSingle(pool, ...)`.
4. The pool passes `sender = router` to the extension.
5. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. The unauthorized user's swap executes against the pool at oracle prices.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool. Once bypassed, any address can execute swaps, extracting value from LPs at oracle-determined prices. LP principal is directly at risk: the pool was configured to trade only with trusted counterparties, but the guard is rendered inoperative for all router-mediated swaps. This is a broken core pool functionality causing direct loss of LP assets.

---

### Likelihood Explanation

Allowlisting the router is the expected operational action for any pool admin who wants to support router-mediated swaps for their allowlisted users. The admin believes they are enabling "router access for allowlisted users" but are actually enabling "router access for all users." No privileged or malicious setup is required beyond the admin performing a routine configuration step. Any user who discovers the router is allowlisted can exploit this immediately.

---

### Recommendation

The extension must check the **end user** identity, not the intermediary caller. Two options:

1. **Pass end-user identity through the router**: The router should forward `msg.sender` (the end user) as a dedicated `swapper` field in `extensionData`, and the extension should decode and check that field. This requires a coordinated interface change.

2. **Check `sender` against the allowlist only when `sender` is not a known router**: The extension can maintain a registry of trusted routers and, when `sender` is a router, require the extension data to contain a signed or verified end-user identity.

The simplest correct fix is to have the pool pass the **original end-user** rather than `msg.sender` as the `sender` argument to extensions, but this requires a protocol-level change to `MetricOmmPool.swap()` and the router to forward the originating user.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (beforeSwap order = extension 1)
  - allowedSwapper[pool][alice] = true          // alice is the intended user
  - allowedSwapper[pool][router] = true         // admin enables router for alice

Attack:
  - bob (not allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: bob, ...})
  - router calls pool.swap(bob, ...) with msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true → PASSES
  - bob's swap executes, draining LP funds at oracle price

Expected: revert NotAllowedToSwap
Actual:   swap succeeds for any user
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```
