### Title
`SwapAllowlistExtension` gates the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract address**, not the actual end-user. The allowlist therefore gates the router as a single entity. If the pool admin allowlists the router (the natural configuration for router-mediated pools), every user — including those not individually allowlisted — can bypass the guard and swap freely.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, amount, ..., extensionData)   [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

where `msg.sender` = pool and `sender` = **router**. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender` into the `sender` slot: [4](#0-3) 

The same flaw applies to `exactInput` (multi-hop) and `exactOutputSingle`: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` intends to restrict swapping to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). To allow those users to also use the standard router, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, the guard is completely neutralised: **any address** can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it only sees the router address. Unauthorized users can then swap against the pool's LP reserves at oracle-derived prices, extracting value from LPs who deposited under the assumption that access was restricted.

---

### Likelihood Explanation

The scenario requires:
1. A pool configured with `SwapAllowlistExtension` in its `BEFORE_SWAP_ORDER`.
2. The pool admin having allowlisted the router (the natural and expected configuration for any pool that wants to support standard periphery access for its allowlisted users).

Both conditions are part of the intended production deployment pattern described in the periphery README and the extension pivot targets in `generate_scanned_questions.py`: [6](#0-5) 

No privileged attacker role is required; any EOA can call the public router.

---

### Recommendation

The pool must convey the **original caller's identity** to the extension, not the intermediary's address. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: Define a convention where the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that value when `sender` is a known router. This requires the extension to trust the router, which must itself be verified.

2. **Dedicated `originator` parameter**: Add an explicit `originator` field to the pool's `swap` signature (or a separate hook argument) that the router populates with `msg.sender` before calling the pool. The extension checks `originator` instead of `sender`. This is the cleanest fix and mirrors how Uniswap v4 separates `sender` from `hookData`-encoded originators.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can swap via router:
//   extension.setAllowedToSwap(pool, address(router), true);

// Attacker (not individually allowlisted) calls the public router:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:           address(pool),
        recipient:      attacker,
        tokenIn:        token0,
        zeroForOne:     true,
        amountIn:       1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:  0,
        deadline:       block.timestamp,
        extensionData:  ""
    })
);
// pool.swap is called with msg.sender = router.
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
// Attacker receives token1 output; allowlist guard is fully bypassed.
```

The `onlyPool` modifier on `BaseMetricExtension` confirms `msg.sender` to the extension is always the pool, so the `sender` argument is the sole identity the extension can inspect — and it is the router, not the user: [7](#0-6)

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
