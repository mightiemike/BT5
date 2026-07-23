Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Full Allowlist Bypass via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted rather than the **end user**. Any pool admin who allowlists the router to support permitted users simultaneously grants every unprivileged user the ability to bypass the allowlist by routing through the same public contract.

## Finding Description
**Pool → Extension sender binding**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**SwapAllowlistExtension check**

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool — the router address when the swap originates through the router: [2](#0-1) 

**Router call site**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router contract `msg.sender` of `pool.swap()`, not the end user: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The mismatch**

| Caller path | `sender` seen by extension | Allowlist entry needed |
|---|---|---|
| Alice calls `pool.swap()` directly | `alice` | `allowedSwapper[pool][alice]` |
| Alice calls `router.exactInputSingle()` | `router` | `allowedSwapper[pool][router]` |
| Bob calls `router.exactInputSingle()` | `router` | `allowedSwapper[pool][router]` |

There is no existing guard in the extension or the router that preserves the end-user identity through the router hop. The `extensionData` field is passed through unchanged but the extension never reads it to recover the originating caller. [2](#0-1) 

## Impact Explanation
Any unprivileged user can execute swaps in a `SwapAllowlistExtension`-gated pool by routing through `MetricOmmSimpleRouter` once the pool admin has allowlisted the router. This breaks the pool's primary access control, allowing unauthorized swaps at oracle-quoted prices. LPs in restricted pools face direct loss of principal through unauthorized extraction of one side of the pool, circumventing the access gate the pool admin believed was enforced. This is a broken admin-boundary / broken core pool functionality finding with direct loss-of-principal risk.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router — a natural and expected configuration for any pool that wants to support standard periphery tooling. Once the router is allowlisted (a one-time admin action), the bypass is trivially reachable by any unprivileged user with no special knowledge or capital beyond the swap amount. The router is a public, permissionless contract.

## Recommendation
The extension must gate the economically relevant actor (the end user), not the intermediate dispatcher (the router). Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. The extension must also verify the claim comes from a trusted router (e.g., via a factory-registered router registry), otherwise any caller can forge the identity.

2. **Separate `originator` field in the pool interface**: Add an `originator` parameter to `pool.swap()` that the router sets to `msg.sender` and the pool forwards to extensions alongside `sender`. Extensions can then choose which identity to gate.

Until one of these is implemented, `SwapAllowlistExtension` cannot safely coexist with the public router on a pool that intends to restrict individual swappers.

## Proof of Concept
```
Setup:
  pool = restricted pool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)
  admin calls setAllowedToSwap(pool, router, true)   ← required for Alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender of pool.swap() = router

  pool calls:
    _beforeSwap(sender=router, ...)

  extension checks:
    allowedSwapper[pool][router] == true  →  passes

  Result: Bob swaps successfully in a pool he was never authorized to access.
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, then call `router.exactInputSingle()` from `bob` and assert the swap succeeds (demonstrating the bypass). Separately, call `pool.swap()` directly from `bob` and assert it reverts with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
