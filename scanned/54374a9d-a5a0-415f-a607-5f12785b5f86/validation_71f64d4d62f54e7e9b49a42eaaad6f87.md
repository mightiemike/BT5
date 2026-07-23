### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` at the pool is the **router address**, not the end user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the pool to every user, rendering the per-user allowlist inoperative.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other router entry points) calls `pool.swap()` directly, making the router the `msg.sender` at the pool: [4](#0-3) 

The result is that the extension checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][actual_user]`. The pool admin faces an inescapable dilemma:

- **Do not allowlist the router** → every allowlisted user is also blocked from using the router.
- **Allowlist the router** → every user on-chain can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or private-pool participants) loses that restriction entirely for any user who calls through the router. Non-allowlisted users can execute swaps at oracle-anchored prices, draining LP value or extracting arbitrage that the allowlist was designed to prevent. This is a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Pool admins who deploy a restricted pool and want to support normal UX (wallets, aggregators, front-ends) must allowlist the router. Once they do, the bypass is trivially reachable by any unprivileged user with zero additional preconditions.

---

### Recommendation

Pass the original end-user address through the swap path so the extension can gate on the actual economic actor. Two options:

1. **Preferred**: Add an explicit `swapper` parameter to `pool.swap()` that callers supply. The pool passes it to `_beforeSwap` instead of `msg.sender`. The router sets `swapper = msg.sender` (the user who called the router). The extension checks `allowedSwapper[pool][swapper]`.

2. **Alternative**: Have the router store the original caller in transient storage and expose a view (`originalSwapper()`) that the extension reads via a pool callback during `beforeSwap`. This avoids changing the core interface but adds complexity.

Either way, the `sender` argument seen by `beforeSwap` must represent the address the pool admin intends to gate, not the intermediate contract.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is the only allowed user)
  allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)   [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives tokens

Result: bob bypasses the allowlist and swaps successfully.
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
