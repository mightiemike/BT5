### Title
`SwapAllowlistExtension` Checks Router Address Instead of Real Swapper, Allowing Any User to Bypass the Curated-Pool Guard — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool receives the **router** as `msg.sender` and forwards the router's address as `sender` to the extension. The extension therefore checks whether the **router** is allowlisted, not whether the actual end-user is allowlisted. If the pool admin allowlists the router so that legitimate users can reach the pool through the standard periphery path, every non-allowlisted user gains the same access by routing through the same contract, completely defeating the curation policy.

---

### Finding Description

**Call chain — direct swap (guard works correctly):**

```
User → pool.swap(recipient, ...) 
  msg.sender = User
  pool calls _beforeSwap(sender=User, ...)
  SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][User]  ✓
```

**Call chain — router-mediated swap (guard is bypassed):**

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
  Router → pool.swap(recipient, ...)
    msg.sender = Router
    pool calls _beforeSwap(sender=Router, ...)
    SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][Router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router's address when the call came through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The pool admin faces an impossible choice:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the standard router at all |
| Router **allowlisted** | Every non-allowlisted user bypasses the guard by routing through the router |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers) is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool and execute swaps that the allowlist was designed to prevent. This constitutes a broken core pool functionality and a direct policy bypass with fund-impacting consequences: unauthorized users can extract value from pools whose liquidity was provided under the assumption that only vetted counterparties would trade.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Any user who reads the contract or observes on-chain transactions can discover the bypass trivially. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The likelihood is **high**.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the end-user — not the intermediary contract. Two sound approaches:

1. **Pass the original caller through the router.** Add a `payer`/`originator` field to the swap call or extension data that the router populates with its own `msg.sender`, and have the extension verify that field instead of (or in addition to) `sender`.

2. **Check `recipient` instead of `sender` when the pool is called via a known router.** This is fragile; option 1 is preferred.

3. **Require direct pool calls for allowlisted pools.** Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call the pool directly. This is operationally limiting but avoids the code change.

The cleanest fix is to have the router forward the original `msg.sender` as part of `extensionData` and have `SwapAllowlistExtension` decode and verify it, with the pool or factory attesting that the router is a trusted forwarder.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Admin allowlists Alice (a legitimate trader) and the router (so Alice can use it).
// Bob is NOT allowlisted.

// Bob calls the router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Pool receives msg.sender = router.
// _beforeSwap(sender=router, ...) is called.
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true (admin allowlisted router).
// Bob's swap executes successfully despite Bob not being on the allowlist.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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
