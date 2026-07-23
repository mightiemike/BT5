### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Unauthorized Users Can Swap in Allowlisted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address (required for any allowlisted user to use the router), every non-allowlisted user can bypass the swap allowlist by routing through the same router.

### Finding Description

**Root cause — wrong identity checked by the allowlist hook**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

**How the router breaks the identity chain**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end-user identity is never visible to the guard.

**The impossible configuration**

The pool admin faces a binary choice with no safe middle ground:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (false negative) | Blocked (correct) |
| Yes | Allowed (correct) | **Allowed (bypass)** |

Allowlisting the router — the only way to let legitimate users reach the pool through the periphery — simultaneously opens the pool to every non-allowlisted address that calls the same router.

The same bypass applies to multi-hop `exactInput` (intermediate hops use `address(this)` as payer, but the first hop still uses `msg.sender` = router) and `exactOutput` (recursive callback swaps also originate from the router): [5](#0-4) 

### Impact Explanation
The swap allowlist is the primary access-control mechanism for pools that restrict counterparties (e.g., KYC-gated pools, institutional pools, or pools that only accept specific market makers). Once the router is allowlisted, the guard is completely ineffective: any address can execute swaps against the pool's liquidity, exposing LP funds to unauthorized flow. This is a direct loss-of-principal risk for LPs who deposited under the assumption that only vetted counterparties could trade.

### Likelihood Explanation
- The trigger is fully unprivileged: any EOA or contract can call `MetricOmmSimpleRouter.exactInputSingle`.
- The precondition (router allowlisted) is the natural, expected configuration for any pool that wants allowlisted users to access the periphery.
- No special timing, oracle manipulation, or admin cooperation is required.

### Recommendation
The `SwapAllowlistExtension` must check the end-user identity, not the intermediary. Two sound approaches:

1. **Extension-data forwarding**: Require the router to encode the originating user address in `extensionData`; have the extension decode and verify it. The pool already threads `extensionData` through unchanged.
2. **Separate user-level allowlist in the router**: Gate `exactInput*` / `exactOutput*` entry points in the router itself against the same allowlist before calling the pool, so the pool-level check becomes redundant for router paths.

Either way, the extension must never treat the router address as the identity to gate.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: allowedSwapper[pool][alice]   = true   (alice is a legitimate user)
  pool admin: allowedSwapper[pool][router]  = true   (required so alice can use the router)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  router calls pool.swap(recipient, ...) — pool sees msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension: allowedSwapper[pool][router] == true  → passes
  bob's swap executes against pool liquidity

Result:
  bob swaps successfully despite never being allowlisted.
  The allowlist guard is fully bypassed for any user who routes through the router.
``` [3](#0-2) [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
