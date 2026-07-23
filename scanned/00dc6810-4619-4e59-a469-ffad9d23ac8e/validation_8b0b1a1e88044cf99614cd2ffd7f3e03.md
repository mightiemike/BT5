After thoroughly reading the extension system, the router, the pool, and all four extension contracts, I have identified a concrete vulnerability.

---

### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the network can bypass the per-user allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the original caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` with itself as `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Therefore the allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → every individually allowlisted user is blocked from using the router.
- **Allowlist the router** → every user on the network can bypass the per-user allowlist by routing through the public router.

There is no code path that lets the pool admin selectively permit specific users to swap via the router while blocking others.

### Impact Explanation
Any user can bypass a pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) on a pool that has `SwapAllowlistExtension` configured. The allowlist guard — the only mechanism the pool admin has to restrict who may trade — is rendered ineffective. Unauthorized users can execute swaps in pools intended for restricted counterparties, disrupting pool dynamics and potentially extracting value from LPs who deposited under the assumption that only vetted parties would trade.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool operator who deploys a restricted pool and also wants router support must allowlist the router, which immediately opens the pool to all users. The trigger is a normal, unprivileged call to a public periphery function with no special preconditions.

### Recommendation
The allowlist must gate the **economic actor**, not the technical caller. Two sound approaches:

1. **Pass the end-user through the router**: Have the router forward the original `msg.sender` in `extensionData`, and have `SwapAllowlistExtension` decode and check that address. This requires a convention between the router and the extension.
2. **Check `sender` and `recipient` together**: Gate on the recipient (the address that receives output tokens) rather than, or in addition to, the sender, since the recipient is the economically relevant party for exact-input swaps.

Alternatively, document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level (e.g., reject pool configurations that combine a swap allowlist with a public router allowlist entry).

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, trustedUser, true)` — only `trustedUser` is meant to swap.
3. Pool admin also calls `setAllowedToSwap(pool, router, true)` — necessary for any router-mediated swap to work.
4. Attacker (not `trustedUser`) calls `router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. Swap executes successfully. The attacker has bypassed the per-user allowlist entirely.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
