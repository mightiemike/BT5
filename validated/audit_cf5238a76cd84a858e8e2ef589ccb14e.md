Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes its own `msg.sender` (the direct caller) as `sender` to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address to enable router-mediated swaps inadvertently grants every public user unrestricted access to the curated pool, completely neutralising the per-user allowlist gate.

## Finding Description

`MetricOmmPool.swap` invokes `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value directly into the call to the extension without modification: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates the swap by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactOutputSingle`, `exactInput`, and `exactOutput`. The admin-facing setter operates on `(pool, swapper)` pairs with no mechanism to distinguish "allow this user through the router" from "allow the router as the terminal swapper": [5](#0-4) 

When a pool admin calls `setAllowedToSwap(pool, router, true)` — the natural action to permit allowlisted users to use the standard periphery — `allowedSwapper[pool][router]` becomes `true`. Every subsequent router-mediated swap passes the check regardless of who the end user is, because the extension always sees `sender = router`.

## Impact Explanation

A non-allowlisted user gains full swap access to a curated pool by routing through `MetricOmmSimpleRouter`. The pool's LP composition, fee income, and price-impact exposure are determined by unrestricted public order flow rather than the curated set the admin intended. This is a direct, repeatable loss of the policy guarantee the pool was deployed to enforce; any resulting adverse-selection or fee-dilution loss falls on LPs. This matches the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" allowed impacts.

## Likelihood Explanation

The trigger is a single, reasonable admin action: `setAllowedToSwap(pool, router, true)`. Any pool admin who wants their allowlisted users to access the pool via the standard router will make exactly this call. There is no on-chain warning, no documentation guard, and no code-level distinction between allowlisting the router as a pass-through versus as a terminal swapper. Once set, the bypass is available to any public user with zero additional privilege and is repeatable indefinitely.

## Recommendation

The pool should forward the original end-user's address rather than `msg.sender` as `sender` to the extension. Two concrete approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of the `sender` parameter.
2. **Transient originator slot**: Store the original caller in a transient slot at the router entry point and expose it via a read function that the extension calls back into.

Either approach ensures the allowlist always gates the economically relevant actor regardless of which supported periphery path reaches the pool.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls: extension.setAllowedToSwap(pool, address(router), true)
   // Admin intent: "let my allowlisted users use the router"
   // Actual effect: allowedSwapper[pool][router] = true

3. Non-allowlisted attacker calls:
   router.exactInputSingle(pool, tokenIn, tokenOut, amountIn, ...)

4. Router calls: pool.swap(attacker, zeroForOne, amount, priceLimit, "", extensionData)
   // pool's msg.sender = router

5. Pool calls: _beforeSwap(msg.sender=router, ...)
   // ExtensionCalling forwards sender=router to extension

6. Extension evaluates: allowedSwapper[pool][router] == true → passes

7. Swap executes for attacker despite attacker not being on the allowlist.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only the router, call `exactInputSingle` from an address not individually allowlisted, assert the swap succeeds (bypass confirmed); then call `pool.swap()` directly from the same address and assert it reverts with `NotAllowedToSwap` (confirming the allowlist is enforced only for direct callers).

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
