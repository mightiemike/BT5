### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. Any pool admin who allowlists the router to enable router-based swaps for legitimate users simultaneously opens the pool to every user on-chain, completely defeating the allowlist.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → ExtensionCalling._beforeSwap(sender=router, ...)
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                             → checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the user enters through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly — it does not forward the original `msg.sender`: [4](#0-3) 

So the extension sees `sender = address(router)`, not `sender = address(user)`. The allowlist lookup becomes `allowedSwapper[pool][router]`.

This creates an inescapable dilemma for the pool admin:

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router at all | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also bypass the allowlist via the router** |

If the admin allowlists the router (the natural choice to enable the supported periphery path), every user on-chain can bypass the allowlist by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router.

The same issue applies to the multi-hop `exactInput` path, where intermediate hops also call `pool.swap` with `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker trades at oracle-derived prices against LP capital that was deposited under the assumption that only allowlisted counterparties could access the pool. This constitutes a direct loss of LP value through unauthorized price-taking and fee dilution on a pool whose entire security model depends on the allowlist.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary production swap entry point documented in the periphery.
- Pool admins who configure `SwapAllowlistExtension` will naturally want allowlisted users to be able to use the router; allowlisting the router is the only way to achieve this.
- No special privilege, flash loan, or unusual token behavior is required — a standard `exactInputSingle` call suffices.
- The bypass is silent: the transaction succeeds with no indication that the allowlist was circumvented.

---

### Recommendation

Pass the original end-user identity through the swap path so the extension can gate on the economic actor rather than the intermediary. Two concrete options:

1. **Router forwards original sender in `extensionData`:** The router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Pool exposes an `initiator` parameter:** Add an explicit `initiator` argument to `pool.swap` that the router populates with `msg.sender`. The extension checks `initiator` instead of `sender`. The pool must validate that `initiator == msg.sender` when called directly (i.e., not through a trusted router).

The cleanest fix is option 2: the pool's `swap` signature gains an `initiator` field, the router passes `msg.sender` there, and `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][initiator]` instead of `allowedSwapper[pool][sender]`.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin allowlists the router so allowedUser can trade via the router.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) bypasses the allowlist via the router:
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// Direct call to pool.swap would revert with NotAllowedToSwap.
// But via the router, sender = router (allowlisted), so it passes:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:           address(pool),
        tokenIn:        address(token0),
        recipient:      attacker,
        zeroForOne:     true,
        amountIn:       1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:  0,
        deadline:       block.timestamp,
        extensionData:  ""
    })
);
// Swap succeeds; attacker receives token1 from the curated pool.
vm.stopPrank();
```

The extension checks `allowedSwapper[pool][router]` (true) and passes, even though `attacker` is not in the allowlist. The pool admin's curation policy is silently defeated.

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
