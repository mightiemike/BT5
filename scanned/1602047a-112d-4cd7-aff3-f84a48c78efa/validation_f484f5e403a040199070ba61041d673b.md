### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `swap` is the **router contract**, not the actual end user. The allowlist therefore checks the router's address rather than the real swapper's identity. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The actual user's address (`msg.sender` of `exactInputSingle`) is stored only in the transient callback context for payment settlement — it is never forwarded to the pool or the extension as the swapper identity.

**Two concrete failure modes arise:**

1. **Allowlist bypass (High):** The admin allowlists the router address so that allowlisted users can reach the pool via the standard periphery. Any non-allowlisted user can then call `router.exactInputSingle()` and the extension sees `sender = router`, which is allowlisted, so the swap succeeds.

2. **Broken core functionality (Medium):** The admin allowlists individual user addresses but not the router. Allowlisted users who call through the router are rejected because the extension sees `sender = router`, which is not in the allowlist. The router — the protocol's own standard swap path — is unusable for curated pools.

Note that `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` argument (the position beneficiary), which `MetricOmmPoolLiquidityAdder` correctly sets to the actual user's address.

### Impact Explanation

**High — direct allowlist bypass enabling unauthorized swaps on curated pools.** A pool configured with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners) can be fully bypassed by any address routing through `MetricOmmSimpleRouter`. The attacker pays no extra cost and needs no special privilege. All swap types (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) are affected.

### Likelihood Explanation

**High.** The router is the protocol's primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and also needs to support router-mediated swaps (the normal case) must allowlist the router, which immediately opens the bypass to all users. The admin has no way to simultaneously allow router-mediated swaps and enforce per-user restrictions using the current extension design.

### Recommendation

Pass the original end-user address through the swap path so the extension can check it. Two approaches:

1. **Preferred — add a `payer` / `originator` field to the swap call or extension data:** Have the router encode the actual user's address in `extensionData` and have `SwapAllowlistExtension` decode and check it. The pool's `onlyPool` guard on the extension already prevents spoofing from outside the pool.

2. **Alternative — check `sender` only for direct calls, decode user from `extensionData` for router calls:** The extension inspects whether `sender` is a known router and, if so, reads the real user from the extension payload.

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can reach the pool via the router.
3. Eve (address not in allowlist) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})
4. Pool calls _beforeSwap(msg.sender=router, ...)
5. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
6. Eve's swap executes successfully despite not being allowlisted.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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
```
