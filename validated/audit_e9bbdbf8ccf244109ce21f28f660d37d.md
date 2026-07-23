Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any Unprivileged Swapper to Bypass the Configured Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is set to `msg.sender` of the `pool.swap(...)` call. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so the extension receives the router's address as `sender`. Any pool admin who enables router-mediated swaps must allowlist the router, which simultaneously grants every user routing through it unrestricted swap access — completely defeating the per-user allowlist gate.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the original end-user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

For any router-mediated swap to succeed on an allowlisted pool, the admin must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router] == true` causes the guard to pass for **every** caller of the router, regardless of whether the actual end-user was ever allowlisted. There is no secondary check on the real originator.

## Impact Explanation

Any user not on the allowlist can bypass the swap gate on a restricted pool by routing through `MetricOmmSimpleRouter`. The pool admin's intent — to restrict swapping to a curated set of addresses — is completely defeated. Non-allowlisted users can execute swaps on pools that were supposed to be closed to them, enabling unauthorized arbitrage or draining of LP value. This is a broken core pool access-control invariant with direct fund-impacting consequences for LPs, matching the "Admin-boundary break bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" allowed impacts.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public entry point for swaps. Any pool using `SwapAllowlistExtension` that also permits router-mediated swaps — the common operational case — must allowlist the router, triggering the bypass. The attacker requires no special privileges, only knowledge of the router address and the pool address. The condition is met automatically whenever the pool admin enables router access.

## Recommendation

The pool should pass the original end-user's address as `sender` rather than `msg.sender`. One approach: require the router to supply the true originator explicitly in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and gate on that value (with the pool verifying the router's identity before trusting the forwarded address). Alternatively, adopt a pattern analogous to Uniswap v4's `msgSender` propagation through unlock callbacks so the pool always has access to the true initiator.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(msg.sender=router, ...)` → `ExtensionCalling` encodes `sender=router` → extension receives `sender=router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `attacker` successfully swaps on a pool they were never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
