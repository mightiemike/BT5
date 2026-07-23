Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks the Router Address Instead of the Originating User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` (the direct pool caller) as `sender` to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than whether the **originating user** is allowlisted. Any disallowed user can bypass a curated pool's access control by calling the router instead of calling `pool.swap()` directly.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`. [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` encodes and forwards `sender` unchanged:**

`_callExtensionsInOrder` is called with `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` — no mechanism exists to inject the original user. [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` gates on `sender` (the direct pool caller):**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool address and `sender` is the first argument — the direct caller of `pool.swap()`. [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender`:**

`exactInputSingle`, `exactOutputSingle`, and `exactInput` all call `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` with no mechanism to pass the originating user. [4](#0-3) 

When Alice calls `exactInputSingle`, the router calls `pool.swap()` with `msg.sender = router`. The extension receives `sender = router` and checks `allowedSwapper[pool][router]` — Alice's allowlist status is never consulted.

## Impact Explanation
A pool operator deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a whitelist of counterparties. The operator must allowlist the router for normal users to trade at all. Once the router is allowlisted, **every user on the network** can swap freely through the router regardless of their individual allowlist status. The pool's LP funds are exposed to any trader the operator intended to exclude, including adversarial actors who could drain liquidity at unfavorable oracle prices. This is a direct bypass of a fund-protecting access control, constituting a High-severity loss of LP principal.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the standard, documented user-facing entry point for swaps.
- Pool operators who deploy `SwapAllowlistExtension` must allowlist the router for normal users to trade, making the bypass trivially reachable.
- No privileged access, no special token, no malicious setup required — any EOA can call the router.
- The bypass is repeatable and unconditional as long as the router is allowlisted.

## Recommendation
The `SwapAllowlistExtension.beforeSwap` hook must gate on the originating user, not the immediate pool caller. Two options:

1. **Check `recipient` instead of `sender`** if the pool's intent is to gate who receives tokens (works for direct swaps where recipient = user, but breaks for multi-hop paths where intermediate recipients are the router itself).
2. **Pass the original user through `extensionData`** and have the router encode the real `msg.sender` into the payload. The extension must then validate that the payload is signed or attested by a trusted router before using it as the identity to check. This is the more robust fix.

The cleaner long-term fix is for the pool to expose an `originSender` field that the router populates and the extension verifies, analogous to how Uniswap v4 hooks receive `hookData` with the original caller.

## Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E.
   - allowAllSwappers[P] = false
   - allowedSwapper[P][router] = true   (router allowlisted so normal users can trade)
   - allowedSwapper[P][alice] = false   (alice is explicitly excluded)

2. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool=P, recipient=alice, ...).

3. Router calls P.swap(recipient=alice, ...) with msg.sender = router.

4. MetricOmmPool.swap() calls _beforeSwap(msg.sender=router, recipient=alice, ...).

5. ExtensionCalling._beforeSwap encodes sender=router and dispatches to E.beforeSwap.

6. E.beforeSwap checks allowedSwapper[P][router] → true → returns success selector.

7. Swap executes. Alice receives tokens from the curated pool she was meant to be excluded from.
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][router] = true` and `allowedSwapper[pool][alice] = false`, then call `exactInputSingle` as Alice and assert the swap succeeds (demonstrating the bypass), then call `pool.swap()` directly as Alice and assert it reverts with `NotAllowedToSwap`.

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
