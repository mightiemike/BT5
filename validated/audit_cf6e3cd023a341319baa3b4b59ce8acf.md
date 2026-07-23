Audit Report

## Title
Router-Mediated Swaps Substitute Router Address for Actual Swapper Identity in SwapAllowlistExtension — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. Any pool that allowlists the router to support router-based trading thereby grants every unprivileged user the ability to bypass the per-user allowlist in a single transaction.

## Finding Description

`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling::_beforeSwap` forwards that value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-177
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)   // sender = pool's msg.sender = router
)
```

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap` directly without forwarding the original caller:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
  params.recipient, params.zeroForOne, ..., params.extensionData
);
// msg.sender inside pool = router address
```

The extension's guard therefore evaluates `allowedSwapper[pool][router]`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

There is no code path that recovers the original `msg.sender` of the router call. The pool admin faces an impossible choice: do not allowlist the router (allowlisted users cannot use the router at all), or allowlist the router (every unprivileged user bypasses the per-user allowlist by routing through it).

## Impact Explanation

Any pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) has its guard rendered ineffective the moment the router is allowlisted. An unprivileged attacker calls `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` through the router and the hook passes because it sees the router address, not the attacker's address. This is a broken core access-control mechanism that directly enables unauthorized swaps on pools designed to be restricted — matching the "broken core pool functionality" and "admin-boundary bypass by an unprivileged path" impact criteria.

## Likelihood Explanation

The router is the standard public entrypoint for swaps. Any pool using the allowlist extension that also wants to support router-based trading (a normal operational requirement) will allowlist the router, triggering the bypass for all users. The attacker needs no special privileges, no flash loan, and no multi-block setup — a single transaction through the router suffices.

## Recommendation

The extension must receive the original initiator address, not the immediate caller of `pool.swap`. The most robust fix is for the router to store `msg.sender` in transient storage (it already does this for the callback payer via `_setNextCallbackContext`) and expose it via a trusted registry or pass it through `extensionData`, so the pool can forward the true originator as `sender` to the hook. Alternatively, document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router and that allowlisted users must call `pool.swap` directly — but this is operationally fragile and not enforceable on-chain.

## Proof of Concept

```solidity
// Setup
swapAllowlist.setAllowedToSwap(pool, alice, true);   // only alice is intended
swapAllowlist.setAllowedToSwap(pool, router, true);  // admin adds router so alice can use it

// Attack (single transaction, no timing required, called by attacker EOA)
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    amountIn: X,
    ...
}));
// pool.swap() is called with msg.sender = router
// _beforeSwap(sender=router, ...)
// extension checks: allowedSwapper[pool][router] == true → passes
// Attacker swaps on a pool they were never meant to access
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
