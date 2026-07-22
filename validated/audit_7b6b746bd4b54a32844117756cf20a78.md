### Title
`SwapAllowlistExtension` checks the router address as the swapper identity instead of the actual user, allowing any unprivileged caller to bypass the per-user allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the actual user. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the gate to every user, defeating the per-user curation the extension was deployed to enforce.

### Finding Description

`MetricOmmPool::swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension::beforeSwap` then gates the swap on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter::exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the originating user. The extension has no visibility into who called the router.

The same misbinding occurs in the multi-hop `exactInput` path for every hop after the first (where `address(this)` — the router — is the payer/caller): [5](#0-4) 

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to support the standard router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **every user** — including those the admin explicitly excluded — can call `exactInputSingle` or `exactInput` and the extension will approve the swap because it sees the allowlisted router address, not the user's address. The per-user curation is completely nullified. On a pool offering tight oracle-anchored spreads to a restricted set of counterparties, unauthorized traders can extract value from LPs at those favorable prices.

### Likelihood Explanation

Medium. The trigger is a pool admin making the reasonable operational decision to allowlist the router so that their allowlisted users can access the standard periphery. This is the expected configuration for any production curated pool that intends to support the router. No privileged exploit or malicious setup is required beyond the attacker simply calling the public router.

### Recommendation

The extension must gate on the **economically relevant actor**, not the intermediary. Two sound approaches:

1. **Pass the originating user through the router.** The router stores `msg.sender` in transient storage as the payer; expose it as a separate `originator` field in the swap call or extension payload so the extension can check it.
2. **Check `recipient` instead of `sender` for the swap allowlist**, if the pool's curation intent is to restrict who receives output (though this changes semantics).
3. **Alternatively**, document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this at the factory/initialization level so the combination cannot be deployed.

### Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // enable router support
4. charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Charlie's swap executes on the curated pool despite never being allowlisted.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
