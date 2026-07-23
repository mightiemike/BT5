### Title
SwapAllowlistExtension Bypassed via Router: `sender` Identity Mismatch Allows Unauthorized Swaps - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the actual user. A pool admin who allowlists the router address to enable router-mediated swaps for legitimate users inadvertently opens the pool to every user, defeating the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` value and dispatches it to each extension in order: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` as `msg.sender`: [4](#0-3) 

So the extension receives `sender = address(router)`, not the actual user. The pool admin faces an impossible choice:

- **Option A**: Allowlist only specific users (not the router) → those users cannot use the router at all; their router-mediated swaps revert with `NotAllowedToSwap`.
- **Option B**: Allowlist the router address → every user on the network can call the router and bypass the per-user allowlist, because the extension sees `sender = router` which is allowlisted.

The `DepositAllowlistExtension` does **not** share this flaw — it correctly gates on `owner` (the LP position holder), which is explicitly passed through the call chain and is not overwritten by the router: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a private institutional pool, a KYC-gated pool, or a pool with favorable oracle pricing reserved for specific LPs) can be fully opened to any user by routing through the public `MetricOmmSimpleRouter`. Any user who calls `exactInputSingle`, `exactInput`, or `exactOutputSingle` on the router against such a pool will have their swap processed as if the router itself is the swapper. If the router is allowlisted (the only way to let legitimate users use the router), the allowlist is nullified. Unauthorized users can trade at the pool's oracle-anchored prices, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade against them.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public entry point for swaps. Any user who discovers a restricted pool with favorable pricing has a direct, permissionless path to bypass the allowlist by calling the router. No privileged access, flash loan, or special setup is required. The trigger is a standard `exactInputSingle` call.

---

### Recommendation

The extension must resolve the true initiator of the swap, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the original initiator explicitly**: Modify the `beforeSwap` hook signature or use `extensionData` to carry the original `msg.sender` from the router, and have the extension verify that value instead of `sender`.
2. **Check `recipient` or use a dedicated identity field**: Redesign the allowlist to gate on an identity that is invariant across direct and router-mediated paths (e.g., require the router to forward the original initiator in `extensionData` and have the extension decode and check it).
3. **Allowlist the router with a secondary per-user check**: Require the router to include a signed or verified user identity in `extensionData` that the extension validates, rather than relying on `sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary to allow any router-mediated swap)
  - Pool admin does NOT allowlist attacker address

Attack:
  - attacker calls router.exactInputSingle({pool: restrictedPool, ...})
  - router calls pool.swap(...) with msg.sender = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes successfully for attacker
  - Attacker trades at oracle-anchored prices in a pool intended to be restricted
```

Alternatively, if the admin does not allowlist the router:
```
  - Legitimate allowlisted user calls router.exactInputSingle(...)
  - Extension checks allowedSwapper[pool][router] → false
  - Swap reverts: legitimate user cannot use the router at all
```

Both outcomes break the invariant that the allowlist gates the economically relevant swapper.

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
