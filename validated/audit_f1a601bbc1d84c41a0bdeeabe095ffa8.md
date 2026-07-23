Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded from the pool, which is `msg.sender` of the `pool.swap()` call — the `MetricOmmSimpleRouter` address when users route through the periphery. Any pool admin who allowlists the router to permit router-mediated swaps simultaneously grants unrestricted swap access to every user on the network, completely defeating the per-user access control the extension is designed to enforce.

## Finding Description

**Root cause:** `MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives this value as `sender` and uses it as the identity to look up in the allowlist: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [3](#0-2) 

At the pool level, `msg.sender` is the router contract. The `sender` forwarded to `SwapAllowlistExtension.beforeSwap` is therefore the router address, not the end-user. The same applies to multi-hop `exactInput`: [4](#0-3) 

**Why existing checks fail:** The allowlist mapping is `allowedSwapper[pool][sender]`. When the router is allowlisted (the only way to permit any router-mediated swap), `allowedSwapper[pool][router] == true` satisfies the check for every caller of the router — there is no mechanism to distinguish individual users at this layer.

The unit test confirms the extension gates on the `sender` positional argument: [5](#0-4) 

## Impact Explanation

Any pool configured with `SwapAllowlistExtension` and a curated allowlist (KYC-gated, institutional, single-market-maker) can be freely accessed by any unprivileged user by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The attacker receives real token output from the pool's liquidity at oracle-anchored prices. LP providers suffer direct loss of principal because swaps intended to be restricted to trusted counterparties are executed by arbitrary actors. This constitutes a direct loss of user/LP principal and broken core pool access-control functionality — Critical/High severity under Sherlock thresholds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point. Any pool deploying `SwapAllowlistExtension` that also wants to support router-mediated swaps (the normal production configuration) must allowlist the router, triggering the bypass. The attacker requires no special privileges, no flash loan, and no oracle manipulation — a single standard router call suffices. The condition is self-inflicted by the protocol's own recommended usage pattern.

## Recommendation

Gate on the economically relevant actor rather than the immediate pool caller. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it against the allowlist. Requires a trusted-router convention.
2. **Check `recipient` instead of (or in addition to) `sender`**: The recipient is the party receiving value from the swap. Gating on `recipient` prevents value extraction by unauthorized parties regardless of routing path, and `recipient` is already available as the second argument to `beforeSwap`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (allowAll = false).
  - Admin calls setAllowedToSwap(pool, alice, true).
  - Admin calls setAllowedToSwap(pool, router, true)  ← required for Alice to use the router.

Attack:
  1. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...).
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...):
       allowedSwapper[pool][router] == true → passes.
  5. Swap executes; Bob receives token output from the restricted pool.

Result: Bob, an unprivileged address, extracts value from a pool restricted to Alice only.
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-30)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
