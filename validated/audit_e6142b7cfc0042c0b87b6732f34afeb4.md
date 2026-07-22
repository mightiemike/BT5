### Title
`SwapAllowlistExtension` gates on the router address (`sender`) instead of the end user, allowing any user to bypass the per-user swap allowlist through the standard periphery — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call — the router, not the end user. When a pool admin allowlists `MetricOmmSimpleRouter` so that authorized users can access the pool through the standard periphery, every user on-chain can bypass the per-user allowlist by routing through the same router.

---

### Finding Description

**Actor binding in the pool:**

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller of `pool.swap()`) as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` forwards that value unchanged to the extension: [2](#0-1) 

**The guard in the extension:**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

**The router call chain:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` with `msg.sender = router`: [4](#0-3) 

The full call chain is:

```
end-user → router.exactInputSingle(pool, ...)
         → pool.swap(recipient, ...)          [msg.sender = router]
         → extension.beforeSwap(sender=router, ...)
         → checks allowedSwapper[pool][router]
```

The extension **never sees the end user's address**. It only sees the router.

**The dilemma this creates for pool admins:**

| Admin action | Outcome |
|---|---|
| Allowlist the router | Every user on-chain can bypass the per-user allowlist by calling the router |
| Do not allowlist the router | No user can swap through the standard periphery, even allowlisted ones |

There is no configuration that achieves the intended behavior: "only allowlisted users may swap, including through the router."

---

### Impact Explanation

A curated pool with `SwapAllowlistExtension` is designed to restrict swaps to specific authorized addresses (e.g., KYC-verified wallets, whitelisted institutions). Because the extension keys authorization to the router address rather than the end user, any non-allowlisted user can execute swaps on the curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist is rendered completely ineffective for the standard periphery path, which is the primary user-facing entry point. This is a direct curation failure with fund-impacting consequences: unauthorized parties can trade on pools that were intended to be restricted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the production swap interface. Any pool admin who wants their allowlisted users to be able to use the standard UI/SDK must allowlist the router. This is the natural and expected configuration. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The extension must check the actual end user, not the intermediary. Two viable approaches:

1. **Pass the end user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`. The extension decodes and verifies it. This requires a convention between the router and the extension.

2. **Add a `payer`/`originator` field to the swap hook signature:** Extend `IMetricOmmExtensions.beforeSwap` to include the originating user address as a separate argument, populated by the pool from a router-supplied context (e.g., a transient storage slot set by the router before calling `pool.swap()`).

Until fixed, pool admins should be warned that `SwapAllowlistExtension` cannot enforce per-user restrictions when the pool is accessible through `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary for any user to use the standard periphery)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  - router calls pool.swap(attacker, ...) with msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → true → passes
  - swap executes; attacker receives output tokens

Result:
  - attacker, who is not on the allowlist, successfully swaps on a curated pool
  - the per-user allowlist is completely bypassed
```

The same bypass applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput` paths in `MetricOmmSimpleRouter`, since all of them call `pool.swap()` with `msg.sender = router`. [3](#0-2) [1](#0-0) [5](#0-4)

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
