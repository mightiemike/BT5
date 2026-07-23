Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity instead of originating user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the router is allowlisted rather than the originating user. A pool admin who allowlists the router to support router-mediated swaps for their KYC'd users inadvertently opens the gate to every user, completely nullifying the per-user curation the extension was deployed to enforce.

## Finding Description
**Root cause — `MetricOmmPool.swap()` passes `msg.sender` as `sender`:**

`MetricOmmPool::swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

**Root cause — `SwapAllowlistExtension` checks `sender` directly:**

`SwapAllowlistExtension::beforeSwap` gates the swap on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Exploit path — router is the direct caller of `pool.swap()`:**

In `exactInputSingle`, the router calls `pool.swap()` directly, making the router the `msg.sender` to the pool: [4](#0-3) 

For multi-hop `exactInput`, hops after the first use `address(this)` (the router) as the payer/caller, so the same misbinding occurs on every subsequent hop: [5](#0-4) 

**Why existing guards fail:** The extension has no mechanism to look through the router to the originating user. The `allowedSwapper` mapping is keyed on the direct caller of `pool.swap()`, which is always the router when the standard periphery is used. There is no transient storage read, no `originator` field, and no alternative check path in `beforeSwap`.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` must allowlist the router (`allowedSwapper[pool][router] = true`) to allow their KYC'd users to access the pool via the standard periphery. Once the router is allowlisted, every user — including those explicitly excluded — can call `exactInputSingle` or `exactInput` and the extension approves the swap because it sees the allowlisted router address, not the user's address. The per-user curation is completely nullified. On a pool offering tight oracle-anchored spreads to a restricted set of counterparties, unauthorized traders can extract value from LPs at those favorable prices. This constitutes a broken core pool functionality (allowlist enforcement) causing potential loss of funds to LPs through unauthorized access to favorable pricing.

## Likelihood Explanation
Medium. The trigger is a pool admin making the reasonable and expected operational decision to allowlist the router so that their allowlisted users can access the standard periphery. This is the expected configuration for any production curated pool that intends to support router-mediated swaps. No privileged exploit, malicious setup, or non-standard behavior is required — the attacker simply calls the public router. The condition is likely to occur in any real deployment of a curated pool.

## Recommendation
The extension must gate on the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the originating user through the router.** The router stores `msg.sender` in transient storage as the payer via `_setNextCallbackContext`; expose it as a separate `originator` field in the swap call or extension payload so the extension can check it instead of `sender`.
2. **Document incompatibility and enforce at factory/initialization level.** If the fix is deferred, prevent the combination of `SwapAllowlistExtension` and `MetricOmmSimpleRouter` from being deployed together by enforcing this constraint at the factory or pool initialization level so the misconfiguration cannot occur in production.

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router support for alice
4. charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Charlie's swap executes on the curated pool despite never being allowlisted.

Foundry test plan:
- Deploy SwapAllowlistExtension and a MetricOmmPool configured with it.
- Call setAllowedToSwap(pool, alice, true) and setAllowedToSwap(pool, router, true).
- Prank as charlie (not allowlisted) and call router.exactInputSingle(...).
- Assert the swap succeeds (no NotAllowedToSwap revert), confirming the bypass.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
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
