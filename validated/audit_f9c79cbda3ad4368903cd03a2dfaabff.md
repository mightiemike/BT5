Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any caller to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to the `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that value is the router contract address, not the end user. A pool admin who allowlists the router to enable router-based swaps on a curated pool inadvertently grants swap access to every user of the router, completely defeating the per-address curation policy.

## Finding Description

**Root cause in `SwapAllowlistExtension.beforeSwap`**

The extension gates swaps with:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool via `_callExtensionsInOrder`) and `sender` is the first argument forwarded by the pool — the `msg.sender` of the `pool.swap()` call. [1](#0-0) 

**How `sender` is populated**

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

**The router is the `msg.sender` of `pool.swap()`**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router contract the `msg.sender`: [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The bypass**

A pool admin deploying a curated pool (KYC, institutional-only, whitelist-only) who also wants to support standard router-based swaps will call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call arriving through the router, regardless of who the actual end user is. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle()` and the extension passes because it sees the allowlisted router address, not the unapproved user.

The broken invariant: `allowedSwapper[pool][user]` is supposed to gate the economic actor, but it gates the intermediary instead. The exact wrong value is `sender` inside `beforeSwap` — it is the router address, not the user address. [5](#0-4) 

## Impact Explanation

Any user can trade on a curated pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`, as long as the router is allowlisted. The pool's curation policy (KYC, institutional-only, whitelist-only) is completely bypassed. This constitutes a broken core pool functionality and an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) circumvents the access control the pool admin configured. The impact is direct — unauthorized users execute swaps on pools they were never authorized to access.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a curated pool and wants to support standard router-based swaps will naturally allowlist the router — there is no documentation or interface warning that doing so opens the allowlist to all router users. The misconfiguration is a predictable consequence of normal pool setup, requiring no special attacker capability beyond calling the public router.

## Recommendation

The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` (the end user) into `extensionData` and have the extension decode and verify it. This requires a coordinated convention between the router and the extension.
2. **Detect known routers and require `extensionData` identity**: The extension can check whether `sender` is a known router and, if so, require the user address to be present and verified in `extensionData`.
3. **Document the limitation explicitly**: If the design intent is to gate the immediate caller of `pool.swap()`, the extension documentation must explicitly state that allowlisting the router grants access to all router users, and pool admins must allowlist individual users who call the pool directly rather than allowlisting the router.

## Proof of Concept

```
Setup:
  - Pool P is deployed with SwapAllowlistExtension E.
  - Pool admin calls E.setAllowedToSwap(P, alice, true)   // Alice is KYC'd
  - Pool admin calls E.setAllowedToSwap(P, router, true)  // enable router path

Attack:
  - Bob (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  - Router calls P.swap(recipient, ...) — msg.sender = router
  - Pool calls E.beforeSwap(router, recipient, ...)
  - Extension checks: allowedSwapper[P][router] == true  ✓
  - Swap executes successfully for Bob despite Bob not being in the allowlist.

Result:
  - Bob trades on a curated pool he was never authorized to access.
  - Alice's KYC-gated pool is open to the public via the router.
```

A Foundry test can reproduce this by deploying a pool with `SwapAllowlistExtension`, allowlisting only `alice` and the router, then calling `exactInputSingle` from `bob` and asserting the swap succeeds without revert.

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
