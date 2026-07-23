Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. Any pool admin who allowlists the router to enable legitimate router-based swaps simultaneously opens the allowlist to every user, completely defeating the curated pool's access control.

## Finding Description

**Root cause — wrong actor bound in `SwapAllowlistExtension.beforeSwap`:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when user routes through it
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards it verbatim to the extension via `abi.encodeCall`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks that forwarded address against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without encoding the original `msg.sender` into `extensionData` or any other argument visible to the extension:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← user-supplied, router does not inject original msg.sender
);
```

The original user's identity is stored only in transient storage for the payment callback and is never forwarded to the pool or the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

**The inescapable dilemma for pool admins:**

| Admin action | Result |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router — broken core functionality |
| Allowlist the router | Every user can bypass the allowlist by routing through it |

No configuration simultaneously allows legitimate users to use the router and blocks non-allowlisted users.

**Concrete bypass path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`, sets `allowAllSwappers[pool] = false`, and sets `allowedSwapper[pool][alice] = true`.
2. To let Alice use the router, admin also sets `allowedSwapper[pool][router] = true`.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(...)` → pool passes `sender = router` → extension checks `allowedSwapper[pool][router] = true` → passes.
5. Bob's swap executes on the curated pool.

## Impact Explanation
This is an admin-boundary break and broken core pool functionality. LPs who provide liquidity to a curated pool at tighter spreads — trusting that only vetted counterparties can trade — are exposed to arbitrary, potentially adversarial traders. Non-allowlisted users can extract value from LPs who expected a restricted trading environment. The allowlist extension, a production guard, fails to enforce its invariant on the primary supported periphery path (`MetricOmmSimpleRouter`), which is the main user-facing swap interface.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface; most users will route through it. A pool admin who wants legitimate users to use the router has no choice but to allowlist the router address, which opens the bypass to everyone. No special privileges or unusual conditions are required — any EOA can call the router. The bypass is repeatable and unconditional once the router is allowlisted.

## Recommendation
The extension must gate the economically relevant actor, not the immediate pool caller. The most robust fix is for `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` before calling `pool.swap`, and for `SwapAllowlistExtension.beforeSwap` to decode and check it when the caller is a known trusted router. Alternatively, add an allowlist check inside `MetricOmmSimpleRouter` before calling `pool.swap`, so the router enforces the same policy using the original `msg.sender`. The core invariant that must hold: a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin: only alice can swap
ext.setAllowedToSwap(pool, alice, true);
// Admin: allowlist router so alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) routes through the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    extensionData: ""
}));
// ✓ succeeds — bob bypassed the allowlist
// Extension checked allowedSwapper[pool][router] = true, not allowedSwapper[pool][bob]
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
