### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the original user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument passed by the pool — which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the original user. If the pool admin allowlists the router (the natural action to enable router-mediated swaps), every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, ..., extensionData)   [msg.sender = router]
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder(...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The admin-facing setter is named `swapper`, signalling the intent to gate individual users: [4](#0-3) 

When the router calls `pool.swap()`, the router address is what the extension sees as `sender`. The pool admin has two losing options:

1. **Do not allowlist the router** — allowlisted users cannot swap through the router at all; the router is blocked for everyone.
2. **Allowlist the router** — the per-user gate is fully bypassed; any user can swap by routing through `MetricOmmSimpleRouter`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no forwarding of the original caller's identity: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool, draining liquidity at oracle-anchored prices that the pool admin intended to expose only to specific parties. This constitutes broken core pool functionality and a direct bypass of an access-control guard with fund-impacting consequences.

---

### Likelihood Explanation

The router is the primary user-facing swap interface for the protocol. A pool admin who deploys a swap-allowlisted pool and wants allowlisted users to be able to use the standard router will inevitably allowlist the router address. The bypass is then reachable by any address with no special privilege. The only way to avoid it under the current design is to prohibit router use entirely, which defeats the purpose of the periphery.

---

### Recommendation

The `beforeSwap` hook must gate on the **original user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In `SwapAllowlistExtension`**: check `recipient` (the second argument) or require the original user identity to be encoded in `extensionData` and verify it there, rather than trusting `sender`.
2. **In `MetricOmmSimpleRouter`**: forward the original `msg.sender` as part of `extensionData` so allowlist extensions can recover the true initiator.

The cleanest long-term fix is option 2 combined with a convention in `SwapAllowlistExtension` to decode and verify the original caller from `extensionData` when the immediate `sender` is a known router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow allowlisted users to use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  - Router calls pool.swap(recipient, ...) — msg.sender to pool = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  - Swap executes. Attacker bypasses the per-user allowlist.
```

The attacker needs no special role. The only precondition is that the pool admin has allowlisted the router — the natural operational step to enable router-mediated swaps for legitimate users.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
