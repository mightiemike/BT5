### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass the allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the real user. The allowlist therefore gates the router's address rather than the actual swapper, making the guard trivially bypassable by any user who calls the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument to every registered extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol:95-98
_callExtensionsInOrder(
    BEFORE_ADD_LIQUIDITY_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that exact value against its per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(recipient, ...)` directly, making the pool see `msg.sender = router`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is a complete identity mismatch: the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][real_user]`. Two failure modes follow:

1. **Allowlist bypass** — if the pool admin allowlists the router (the natural operational choice so that normal users can swap), every address in the world can swap through the router and pass the guard, defeating the allowlist entirely.
2. **Allowlist lock-out** — if the pool admin allowlists individual user addresses (not the router), those users cannot swap through the router at all, breaking the normal swap UX.

---

### Impact Explanation

The `SwapAllowlistExtension` is a production access-control extension whose sole purpose is to restrict which addresses may execute swaps on a given pool. A bypass of this guard means an unpermissioned address can execute swaps against a pool that was explicitly configured to reject it. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, whitelist-only liquidity), this constitutes a broken core pool functionality and a direct admin-boundary break: the pool admin's configured access policy is silently nullified by any user who routes through the public router.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who wants to swap on an allowlist-protected pool will naturally use the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to a public function that is already the standard swap path.

---

### Recommendation

The extension must check the economically relevant actor, not the immediate `msg.sender` of the pool. Two sound approaches:

1. **Check `sender` against the allowlist but also accept the router as a transparent forwarder only when the router itself passes the real user's address in `extensionData`.** This requires a protocol-level convention.
2. **Preferred — check `sender` directly and require that any allowlisted router also passes the real user identity.** Concretely, the pool or router should forward the original `msg.sender` (the end user) as `sender` rather than the router's own address. The router would need to be redesigned to pass `msg.sender` (the user) as the `sender` argument to `pool.swap`, or the extension must be aware of trusted forwarders and unwrap the real caller from `extensionData`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (or: pool admin calls setAllowAllSwappers(pool, false) and allowlists only router)
  - Alice (address not in allowlist) calls:
      router.exactInputSingle({pool: pool, ...})

Execution trace:
  Alice → router.exactInputSingle()
    → pool.swap(recipient, ...)   [msg.sender = router]
      → _beforeSwap(msg.sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes)
      → swap executes, Alice receives output tokens

Result:
  Alice, who is NOT in the allowlist, successfully swaps on an allowlist-protected pool.
  The guard checked allowedSwapper[pool][router] instead of allowedSwapper[pool][Alice].
```

The `DepositAllowlistExtension` is not affected by the same issue because it checks the `owner` parameter (which is explicitly passed by the caller and represents the LP position owner), not the `sender`. However, `SwapAllowlistExtension` has no equivalent disambiguation — it checks `sender`, which is always the immediate caller of `pool.swap()`, i.e., the router. [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L91-99)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
