### Title
`SwapAllowlistExtension` Gates Router Address Instead of Ultimate User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the direct caller of `pool.swap()` — against the per-pool allowlist. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. If the pool admin allowlists the router to support router-mediated swaps, every user — including those not individually allowlisted — can bypass the per-user swap gate.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension caller) and `sender` is the address the pool received as `msg.sender` when `swap()` was called on it. The pool's `swap` passes its own `msg.sender` as `sender` to the extension — confirmed by the interface NatDoc: *"Swap allowlist rejected `msg.sender`"*. [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The router is `msg.sender` to the pool, so the extension receives `sender` = router address, not the originating user. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap(...)` with the router as `msg.sender`. [4](#0-3) 

The pool dispatches to extensions via `ExtensionCalling._beforeSwap`, forwarding whatever `sender` the pool received: [5](#0-4) 

**The missing path:** The allowlist covers direct `pool.swap()` calls (where `sender` = individual user) but does not cover router-mediated calls (where `sender` = router). This is the exact structural analog to the external bug: `approve()` was tracked but `increaseAllowance()` — an alternate path to the same effect — was not.

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties). To also support `MetricOmmSimpleRouter` for those users, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** address — including those never individually allowlisted — can call any router entry point and successfully swap on the restricted pool. The per-user gate is completely bypassed. Depending on the pool's purpose (e.g., institutional-only liquidity, regulatory compliance), this constitutes a broken core access-control invariant with direct fund-impact potential: unauthorized parties can drain liquidity via swaps the pool was designed to block.

---

### Likelihood Explanation

Medium. The admin must take the affirmative step of allowlisting the router. However, this is a natural and expected action: any admin who wants their allowlisted users to benefit from multi-hop routing or slippage protection via the router will allowlist it. The design gives no indication that doing so opens the pool to all users; the `setAllowedToSwap` parameter is named `swapper`, implying individual-user semantics. The bypass is therefore likely to be triggered inadvertently during normal pool configuration.

---

### Recommendation

The extension must identify the **ultimate user**, not the intermediary. Two viable approaches:

1. **Pass the originating user through `extensionData`:** The router already stores the originating `msg.sender` in transient storage (`_getPayer()`). It could encode this into `extensionData` so the extension can verify it. The extension would then check the decoded user address rather than `sender`.

2. **Separate router-aware allowlist entry:** Add a `setAllowedRouter(pool, router, true)` flag distinct from per-user entries, and require that when `sender` is an allowlisted router, the extension also validates a user address embedded in `extensionData`.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap extension.
2. Admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
3. Admin calls setAllowedToSwap(pool, router, true)      // to let alice use the router
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router.
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes on the restricted pool despite never being allowlisted.
```

The allowlist check at line 37–39 of `SwapAllowlistExtension.sol` evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][bob]`, so the gate is silently bypassed. [6](#0-5)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```
