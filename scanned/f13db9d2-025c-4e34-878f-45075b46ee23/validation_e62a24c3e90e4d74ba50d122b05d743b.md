### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing complete allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin cannot simultaneously (a) allow router-mediated swaps and (b) restrict swaps to a specific set of users: allowlisting the router opens the gate to every user on-chain.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` uses that argument to key into the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The pool admin faces an impossible choice:

| Admin action | Result |
|---|---|
| Allowlist only specific users (not the router) | Allowlisted users cannot use the router at all |
| Allowlist the router to enable router-mediated swaps | Every address on-chain can bypass the allowlist via the router |

There is no configuration that achieves "only allowlisted users may swap, and they may use the router."

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to serve a restricted set of LPs or counterparties (e.g., a private institutional pool) is fully open to any swapper the moment the pool admin allowlists the router. Unauthorized swappers can drain LP-owned token1 or token0 from the pool at oracle-quoted prices, causing direct principal loss to LPs. The allowlist guard — the only access-control layer on the swap path — is rendered inoperative.

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) expects users to interact through `MetricOmmSimpleRouter` (the standard periphery entry point) is affected. The pool admin's natural remediation — allowlisting the router — is exactly the action that triggers the bypass. No special attacker capability is required beyond calling the public router.

### Recommendation

The extension should check the **economically relevant actor** — the end user — not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** The router already stores the original `msg.sender` in transient storage as the payer (`_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, msg.sender, ...)`). The pool could forward this as a separate `originator` field, or the extension could read it from a trusted router registry.

2. **Check `sender` only when it is not a known router; otherwise check the payer stored in transient storage.** This keeps the extension composable without breaking direct-pool callers.

Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level.

### Proof of Concept

```solidity
// 1. Pool admin deploys pool with SwapAllowlistExtension.
// 2. Admin allowlists alice only.
extension.setAllowedToSwap(pool, alice, true);

// 3. Alice tries to swap through the router — REVERTS because
//    the extension sees sender=router, not alice.
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// → NotAllowedToSwap

// 4. Admin allowlists the router to "fix" the UX.
extension.setAllowedToSwap(pool, address(router), true);

// 5. Bob (never allowlisted) now swaps through the router — SUCCEEDS.
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// → swap executes; allowlist completely bypassed
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, which keys on the `sender` argument that equals the router address for all router-mediated swaps. [1](#0-0) [2](#0-1) [3](#0-2)

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
