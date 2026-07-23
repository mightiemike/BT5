### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the originating EOA. If the pool admin allowlists the router address (the only way to permit router-mediated swaps for curated pools), every unprivileged user can bypass the allowlist by routing through the public router.

---

### Finding Description

**Call chain:**

```
EOA (non-allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)          [msg.sender = EOA]
      → IMetricOmmPoolActions(pool).swap(recipient, ...)    [msg.sender = router]
          → MetricOmmPool._beforeSwap(msg.sender=router, recipient, ...)
              → ExtensionCalling._callExtensionsInOrder(BEFORE_SWAP_ORDER, ...)
                  → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                      checks: allowedSwapper[pool][router]  ← router address, NOT the EOA
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no forwarding of the originating EOA: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap` with `msg.sender = router`. [5](#0-4) 

**The structural trap:** A pool admin who wants allowlisted users to be able to use the router has exactly two options, both broken:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted EOAs cannot use the router at all — broken UX |
| Allowlist the router | Every non-allowlisted EOA can bypass the allowlist via the router — security bypass |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) loses that restriction entirely once the router is allowlisted. Any EOA can execute swaps on the pool by routing through `MetricOmmSimpleRouter`, receiving output tokens at the oracle-anchored price. This constitutes a direct policy bypass with fund-impacting consequences: the pool's LP assets are exposed to unrestricted trading by actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the protocol. Any pool admin who deploys a curated pool and wants to support standard tooling (front-ends, aggregators) will allowlist the router. The bypass is then reachable by any EOA with zero privilege — a single `exactInputSingle` call suffices. No special timing, flash loan, or multi-step setup is required.

---

### Recommendation

The extension must gate the **economically relevant actor** — the originating user — not the immediate pool caller. Two viable approaches:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender` for router flows, or require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router and must require direct `pool.swap` calls from allowlisted EOAs.

The cleanest fix is option 1: the router appends the originating EOA to `extensionData`, and the extension reads it when `sender` is a known router address. Alternatively, introduce a dedicated `originalSender` field in the hook signature.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists the router so that allowlisted users can use it
extension.setAllowedToSwap(pool, address(router), true);
// Alice (allowlisted EOA) is also set:
extension.setAllowedToSwap(pool, alice, true);
// Bob (non-allowlisted EOA) is NOT set.

// Attack: Bob routes through the router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             pool,
        recipient:        bob,
        tokenIn:          token0,
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:    0,
        deadline:         block.timestamp,
        extensionData:    ""
    })
);
// Bob's swap succeeds.
// SwapAllowlistExtension checked allowedSwapper[pool][router] == true → passed.
// Bob received oracle-priced token1 output despite not being on the allowlist.
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
