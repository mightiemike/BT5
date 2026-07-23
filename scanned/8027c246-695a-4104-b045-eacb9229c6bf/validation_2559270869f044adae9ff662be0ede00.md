### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any caller to bypass a curated-pool allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is always the router contract, not the end user. The extension therefore gates the router address rather than individual users. If the pool admin allowlists the router (the natural step to enable router-based swaps), every unprivileged user bypasses the per-address restriction.

---

### Finding Description

**Hook dispatch — `sender` is always the direct caller of `pool.swap()`**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**Extension check — keyed on `sender`**

`SwapAllowlistExtension.beforeSwap` uses `sender` (the first argument) as the identity to look up in the per-pool allowlist: [3](#0-2) 

**Router always appears as `sender`**

Every public entry point in `MetricOmmSimpleRouter` calls `pool.swap()` directly from the router contract. For `exactInputSingle`: [4](#0-3) 

For multi-hop `exactInput`, the router is the caller for every hop: [5](#0-4) 

For `exactOutput`, intermediate hops are triggered inside `_exactOutputIterateCallback`, which is also executed on the router: [6](#0-5) 

In every case `msg.sender` of `pool.swap()` = `MetricOmmSimpleRouter`, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address (the only way to let allowlisted users trade via the router) inadvertently opens the pool to **all** callers. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because `allowedSwapper[pool][router] == true`. The pool admin has no mechanism to allowlist specific users for router-mediated swaps; the only choices are "block all router swaps" or "allow all router swaps." The intended per-address curation is completely defeated.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap path. A pool admin who wants allowlisted users to be able to trade normally will allowlist the router as a matter of course. The bypass requires no special privilege, no flash loan, and no unusual token behavior — only a standard router call from any EOA.

---

### Recommendation

Pass the original end-user address through the extension layer. Two concrete options:

1. **Router-side**: Have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a signature or trusted-forwarder pattern).
2. **Extension-side**: Change `SwapAllowlistExtension` to check `sender` only when `sender != address(router)`, and require the actual user identity to be supplied and verified via `extensionData` when the router is the direct caller.

Either approach ensures the extension gates the economically relevant actor (the end user) rather than the intermediary.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin allowlists router: extension.setAllowedToSwap(pool, router, true)
  alice (non-allowlisted EOA) wants to swap

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, ...)          // msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
  → hook passes; alice's swap executes on the restricted pool

Result:
  alice, who was never individually allowlisted, successfully swaps on a
  pool the admin intended to restrict to specific addresses.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
