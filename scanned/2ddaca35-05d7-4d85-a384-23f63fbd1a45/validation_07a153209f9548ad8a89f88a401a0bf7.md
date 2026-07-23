### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the **router contract**, not the originating EOA. The allowlist therefore gates the router's address rather than the actual user. If the router is allowlisted (the only way to let any allowlisted user reach the pool through the router), every non-allowlisted user can also bypass the guard by routing through the same router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever was passed in — the router address when the user entered through `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no forwarding of the originating EOA: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The desync is structural: the pool admin configures the allowlist to gate individual EOAs, but the hook sees only the router's address. The admin faces an impossible choice:

- **Do not allowlist the router** → every allowlisted EOA is blocked from using the router.
- **Allowlist the router** → every non-allowlisted EOA can bypass the guard by routing through the same public router.

### Impact Explanation

Any user who is not on the allowlist can execute swaps against a pool that the admin intended to restrict, by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) with the restricted pool as the target. The pool's LP positions are exposed to swappers the admin explicitly excluded. Depending on the reason for restriction (e.g., protecting against specific counterparties, enforcing KYC, or limiting access during a guarded launch), this constitutes a direct loss of LP-controlled access and potentially of LP principal if the excluded actors are adversarial.

**Severity: Medium** — direct bypass of a configured access-control guard with fund-impacting consequences for LP holders.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will encounter this issue. The attacker needs no special privileges: they only need to call the public router with the target pool address. The bypass is unconditional once the router is allowlisted.

### Recommendation

The `SwapAllowlistExtension` should check the **originating user** rather than the immediate caller of `pool.swap`. Two approaches:

1. **Pass the originating user through the router**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` (the EOA) into `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and verify that value. This requires a trusted encoding convention.

2. **Check `sender` against a router-aware allowlist**: Extend the extension so that when `sender` is a known router, it reads the originating user from the router's transient storage (e.g., via a `IMetricOmmSimpleRouter.getOriginator()` view) and gates on that address instead.

Either way, the invariant must be: **the identity checked by the allowlist is the economically relevant actor who benefits from the swap**, not the intermediate contract that relays the call.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for allowlisted users.
3. `userB` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: restrictedPool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — `msg.sender` = router.
5. Pool calls `extension.beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
6. Swap executes. `userB` has bypassed the allowlist entirely.

Alternatively, if the router is not allowlisted, repeat step 3 with `userA` (individually allowlisted): the swap reverts because `allowedSwapper[pool][router]` is `false`, demonstrating that allowlisted users are also blocked from using the router. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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
