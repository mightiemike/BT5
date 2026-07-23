### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()`. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any legitimate user to trade through it), every unprivileged address can bypass the allowlist by routing through the router.

### Finding Description

The call chain is:

1. `MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap` encodes that `sender` value as the first argument to every configured extension: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The router never forwards the original user's address to the pool. The pool therefore passes the router's address as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]` — it checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

For any legitimate user to trade through the router on a curated pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every call that arrives through the router, regardless of who the actual end user is. The allowlist is structurally bypassed for the entire router-mediated path.

The same structural issue exists in `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router contract: [5](#0-4) 

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties or protocol-controlled accounts). Any address not on the allowlist should be blocked from swapping. Because the extension checks the router's address instead of the user's address, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) and trade freely on the curated pool, receiving pool output tokens and draining LP-owned liquidity at the oracle price. The allowlist provides no protection for router-mediated swaps. This is a direct loss of curation policy and, depending on pool composition, a direct loss of LP principal to unauthorized counterparties.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard public swap entry point documented and deployed alongside the pool. Any user who discovers the allowlist blocks their direct `pool.swap()` call will naturally try the router next. No special knowledge, privilege, or setup is required — the bypass is one public function call away. The only precondition is that the router is allowlisted, which is a necessary operational step for the pool to be usable at all.

### Recommendation

The `sender` identity forwarded to extensions must reflect the economic actor, not the intermediary contract. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` as an explicit `sender` field inside `extensionData`, and have the extension decode and verify it. Alternatively, the pool interface could accept an explicit `sender` override that the router populates with `msg.sender`.

2. **In `SwapAllowlistExtension`**: if the `sender` is a known router, decode the real user from `extensionData` and gate on that address instead. This requires a trusted-router registry or a signed-sender pattern.

The cleanest fix is to add a `sender` override parameter to `pool.swap()` that the router fills with `msg.sender`, and have the pool validate that the caller is an authorized router before accepting the override.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowlisted for legitimate use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes; attacker receives output tokens

Result:
  - attacker, who is not on the allowlist, successfully swaps on the curated pool.
  - The allowlist check passed because it evaluated the router's address, not the attacker's.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
