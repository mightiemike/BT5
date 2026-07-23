### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address — not the actual user's address. If the pool admin allowlists the router (the natural operational choice to enable router-mediated swaps for allowlisted users), every user on the network can bypass the allowlist by routing through the router.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, amountIn, priceLimitX64, "", extensionData)`.
3. Inside `MetricOmmPool.swap`, the pool calls `_beforeSwap(msg.sender, ...)` — here `msg.sender` is the **router**, not the user.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

The pool passes `msg.sender` (the router) as `sender` to the hook: [1](#0-0) 

The extension then checks that `sender` argument against the allowlist: [2](#0-1) 

The router never forwards the original `msg.sender` to the pool — it simply calls `pool.swap(...)` directly, making itself the `msg.sender`: [3](#0-2) 

This is structurally identical to the SKALE bug: the router is the "linked node" that inherits the validator's (allowlisted user's) rights. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user gains the same swap rights by routing through the same public contract.

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). The allowlist is the sole enforcement mechanism. Once the router is allowlisted — which is required for any allowlisted user to use the router — the allowlist is effectively open to the entire public. Any user can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees `sender = router`. The pool's curation policy is permanently broken for the lifetime of the pool (extensions are immutable at pool creation).

**Impact class:** High — broken core pool functionality (allowlist policy bypass), direct unauthorized access to a curated pool's liquidity.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the expected operational path. The bypass is therefore triggered by normal, intended usage and requires no special attacker capability beyond calling a public function.

### Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is user-supplied and not overwritten by the router. However, recipient and swapper are not always the same.
2. **Preferred:** Require the pool to pass the original `msg.sender` through a trusted forwarding mechanism (e.g., the router stores the real payer in transient storage and the pool reads it), or have the router pass the real user as a parameter that the extension can verify. The cleanest fix is to have the pool expose the original caller via a transient slot that extensions can read, similar to how the router already stores `payer` in transient storage.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)  // to enable router-mediated swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  → passes
  - Attacker's swap executes on the curated pool despite never being allowlisted.
``` [4](#0-3) [5](#0-4) [3](#0-2)

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
