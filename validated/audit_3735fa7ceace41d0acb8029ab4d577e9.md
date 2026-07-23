Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass Pool Swap Restrictions via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender` — the router contract — not the end user who initiated the transaction. When a pool admin allowlists the router to enable router-mediated swaps for curated users, every non-allowlisted user gains the same bypass by calling the same public router. There is no configuration that simultaneously permits allowlisted users to swap through the router while blocking non-allowlisted users from doing the same.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` enforces the allowlist against that `sender` argument, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly without passing the original caller's identity: [4](#0-3) 

At the pool call boundary, `msg.sender` is the **router contract**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same substitution occurs in `exactInput` (all hops after the first use `address(this)` as payer): [5](#0-4) 

And in the recursive `_exactOutputIterateCallback` hops: [6](#0-5) 

The router stores `msg.sender` only in transient callback context for payment purposes (`_setNextCallbackContext`), but never passes it to the pool's `swap()` call as a swapper identity. The pool has no mechanism to receive an authenticated "original initiator" field — the `swap()` interface only exposes `recipient`, not a separate `swapper` parameter.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional participants) loses that guarantee entirely for any user who routes through `MetricOmmSimpleRouter`. A non-allowlisted attacker can execute swaps at oracle-derived prices against LP capital that was deposited under the assumption that only vetted counterparties would trade. This constitutes direct loss of LP principal through unauthorized price-taking and fee extraction, and breaks the core pool functionality the extension was configured to enforce. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract. No special role, token balance, or prior interaction is required. Any address can invoke `exactInputSingle` targeting a restricted pool at any time. The bypass requires zero privileged access and is reachable in a single transaction. The only precondition is that the pool admin has allowlisted the router (a necessary step for any allowlisted user to use the router), which is the exact configuration that creates the vulnerability.

## Recommendation
The `sender` identity forwarded to extensions must reflect the economic actor (the end user), not the intermediary contract. Two sound approaches:

1. **Pass the original initiator through the router.** Add a `swapper` parameter to the pool's `swap()` interface that the router populates with `msg.sender` before calling the pool. The pool forwards this value to extensions instead of its own `msg.sender`.

2. **Gate on `msg.sender` inside the router before calling the pool.** The router checks the allowlist itself and reverts for non-allowlisted callers before the pool call is made. This requires the router to be allowlist-aware, coupling periphery to extension logic.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
  - Admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
  - bob is NOT allowlisted

Attack (single transaction, no special privileges):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: restrictedPool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls restrictedPool.swap(bob, true, X, ...)
     → pool.msg.sender = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓ (passes)
  5. Swap executes; bob receives output tokens from LP capital

Result:
  bob, a non-allowlisted address, successfully swaps against a pool
  configured to restrict trading to alice only.
  The allowlist provides zero protection for router-mediated swaps.

Foundry test skeleton:
  - Deploy pool with SwapAllowlistExtension as beforeSwap extension
  - setAllowedToSwap(pool, router, true); setAllowedToSwap(pool, alice, true)
  - vm.prank(bob); router.exactInputSingle({pool: pool, ...})
  - Assert swap succeeds (no NotAllowedToSwap revert)
  - Assert bob received output tokens despite not being allowlisted
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
