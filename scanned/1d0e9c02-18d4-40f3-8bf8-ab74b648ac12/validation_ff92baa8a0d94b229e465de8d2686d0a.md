### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router to support the standard periphery path inadvertently opens the gate to every user on the internet.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that `sender` argument to look up the allowlist, keyed by `msg.sender` (the pool): [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the **router**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls `pool.swap()` with `msg.sender = router`. [5](#0-4) 

The pool admin faces an impossible choice:

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router; they must call the pool directly |
| Router **allowlisted** | Every user on the internet can bypass the allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool that relies on `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise vetted counterparties is fully bypassed the moment the pool admin allowlists the router (the standard, documented periphery path). Any anonymous user can call `MetricOmmSimpleRouter.exactInputSingle` against the pool and execute swaps that the allowlist was designed to prevent. Unauthorized swaps drain LP-owned token reserves at oracle-derived prices, constituting a direct loss of LP principal. The allowlist's entire security guarantee is voided.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a curated pool and wants users to interact through the standard periphery will allowlist the router — this is the natural, expected configuration. The bypass is therefore reachable in the normal operating scenario, not an edge case. Any unprivileged user can trigger it with a single public call.

---

### Recommendation

The extension must gate on the **original end-user**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: ignore the `sender` argument (which is the router when routed) and instead require callers to supply the real user identity in `extensionData`, or use a separate on-chain registry that the router populates in transient storage before calling the pool.

2. **In `MetricOmmSimpleRouter`**: before calling `pool.swap()`, write the original `msg.sender` into a transient slot that the extension can read via `TLOAD`, analogous to how the router already stores the payer in `TransientCallbackPool`. The extension would then read the real user from that slot rather than trusting the `sender` argument.

The simplest correct fix is to have the router store `msg.sender` in a known transient slot before each `pool.swap()` call, and have `SwapAllowlistExtension` read that slot (verifying `msg.sender` is the expected pool) to obtain the true swapper identity.

---

### Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension configured as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intended to let allowlisted users reach the pool through the router.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack
──────
4. Attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      <curated pool>,
           recipient: attacker,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

5. Router calls pool.swap(attacker, true, X, ...) with msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension.beforeSwap checks:
       allowedSwapper[pool][router]  →  true   ✓ (passes)

8. Swap executes. Attacker receives token1 output.
   The allowlist check never touched the attacker's address.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — allowlist fully bypassed
``` [3](#0-2) [1](#0-0) [4](#0-3)

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
