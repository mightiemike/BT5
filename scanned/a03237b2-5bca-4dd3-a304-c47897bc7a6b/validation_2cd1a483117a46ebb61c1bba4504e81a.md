### Title
SwapAllowlistExtension Checks Router Address Instead of Actual End User, Allowing Any User to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate caller of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end user. A pool admin who allowlists the router (required for any router-mediated swap to succeed) inadvertently opens the gate to every user, defeating the per-user curation the extension is designed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The router never forwards the original `msg.sender` (the actual end user) to the pool. There is no mechanism in the router or the extension to recover the real user identity.

**Consequence — two broken states:**

| Pool admin configuration | Outcome |
|---|---|
| Allowlists individual users **and** the router (needed for router swaps) | Any user can bypass the per-user gate by routing through the router |
| Allowlists individual users but **not** the router | Allowlisted users cannot use the router at all |

Neither state matches the intended "only allowlisted users may swap" invariant.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) can be accessed by any arbitrary user simply by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The attacker receives pool output tokens at the oracle-anchored price, draining liquidity that was reserved for allowlisted participants. This constitutes a direct loss of LP assets and a broken core pool functionality (the allowlist guard fails open).

---

### Likelihood Explanation

The router is the standard, documented user-facing entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is the natural configuration, making the bypass reachable by any unprivileged user with no special setup. The attacker only needs to call the public router with a valid swap path.

---

### Recommendation

The `sender` identity passed through the hook must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` (or a dedicated field) so extensions can recover the real user.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the real user from `extensionData` when `sender` is a known periphery contract, or the pool/router architecture should guarantee that `sender` always equals the end user (e.g., by having the router pass `msg.sender` as the `sender` argument to `pool.swap` rather than relying on `msg.sender` of the pool call).

The analogous fix in the PheasantNetwork report validates the caller-supplied index against the evidence structure; here the fix is to validate the caller-supplied `sender` against the actual economic actor.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist only Alice.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that Alice can use the router (otherwise Alice's router calls revert).
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
4. The router calls `pool.swap(bob, ...)` with `msg.sender = router`.
5. The pool calls `extension.beforeSwap(sender=router, ...)`.
6. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully, bypassing the per-user allowlist. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
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
