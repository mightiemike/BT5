### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Any User to Bypass Per-User Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router address**, not the actual user. Any non-allowlisted user can therefore bypass a curated pool's per-user swap allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
       (msg.sender = router)
     → _beforeSwap(sender = router, ...)
     → SwapAllowlistExtension.beforeSwap(sender = router, ...)
       checks allowedSwapper[pool][router]   ← NOT the actual user
```

The extension never sees the real user's address. The allowlist check is performed against the router contract address. This creates two broken states:

1. **Router not allowlisted**: All router-mediated swaps revert, even for legitimately allowlisted users — the allowlist breaks the router entirely.
2. **Router allowlisted** (the only way to allow router-mediated swaps for legitimate users): Every user on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees only the router address and passes it.

The `DepositAllowlistExtension` does not share this flaw because it gates on the `owner` argument (explicitly supplied by the caller), not on `sender`/`msg.sender`.

### Impact Explanation

A pool admin who deploys a curated pool (e.g., for KYC'd institutional traders, whitelisted market makers, or restricted LP counterparties) and configures `SwapAllowlistExtension` with a per-user allowlist cannot enforce that allowlist for any user who routes through `MetricOmmSimpleRouter`. Any non-allowlisted address can trade on the curated pool by calling the public router. This is a direct, complete bypass of the configured access-control guard, allowing unauthorized parties to drain LP liquidity at oracle-anchored prices from a pool that was designed to be restricted.

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. No special privilege, token balance, or setup is required to call it. Any user who observes that a pool has a swap allowlist can immediately bypass it by routing through the router. The bypass requires a single transaction and zero preconditions beyond having the input token.

### Recommendation

The extension must gate on the **economically relevant actor** — the end user — not on the intermediary router. Two viable approaches:

1. **Pass the original initiator through the router**: Have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a trust assumption that the extension only accepts this encoding from known routers.

2. **Check `sender` against a router registry and fall back to `extensionData`**: If `sender` is a known periphery router, decode the real initiator from `extensionData`; otherwise check `sender` directly.

3. **Align with the deposit allowlist pattern**: Require the pool's `swap()` interface to accept an explicit `swapper` identity argument (analogous to `owner` in `addLiquidity`), so the router can forward the real user address as a first-class parameter rather than relying on `msg.sender`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required to allow any router-mediated swaps for legitimate users)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: curated_pool,
          tokenIn: token0,
          zeroForOne: true,
          amountIn: X,
          amountOutMinimum: 0,
          ...
      })
  - pool.swap(msg.sender=router, ...) is called
  - _beforeSwap(sender=router, ...) is dispatched
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for the non-allowlisted attacker
```

The allowlist is completely bypassed. The attacker receives oracle-priced output tokens while the LP position absorbs the trade, with no recourse since the transaction succeeds on-chain. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-83)
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
