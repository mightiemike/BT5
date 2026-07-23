### Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps for curated users, every unprivileged user can bypass the allowlist by routing through the same router.

---

### Finding Description

**Actor binding in the extension:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

**What the pool passes as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of the pool) as the `sender` argument to every before-swap hook:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

**What the router passes as `msg.sender` to the pool:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool therefore sees `msg.sender = router`, not the originating EOA:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The bypass path:**

A pool admin who wants allowlisted users to be able to trade through the router must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the extension sees `sender = router` for every swap that arrives through it — regardless of who the originating caller is. Any non-allowlisted user can therefore call `exactInputSingle` (or any other router entry point) and the extension will pass because it only checks whether the router itself is allowed.

The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards the pool's `msg.sender` as `sender` with no mechanism to recover the original EOA: [5](#0-4) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for the router path the moment the admin allowlists the router. Any unprivileged address can execute swaps against the pool, draining LP value at oracle-anchored prices that the pool's LPs accepted only under the assumption that only vetted counterparties would trade. This is a direct policy bypass with potential for LP principal loss on pools designed for restricted access (e.g., institutional or KYC-gated pools).

---

### Likelihood Explanation

The trigger is a two-step sequence that is entirely plausible in production:

1. Pool admin allowlists the router — a natural action when the pool is meant to be accessible via the standard periphery for allowlisted users.
2. Any non-allowlisted user calls the router — zero additional privilege required.

The admin has no way to allowlist specific users *for router-mediated swaps only* without simultaneously opening the pool to all router callers. The design gap is structural, not a misconfiguration edge case.

---

### Recommendation

The router must propagate the originating user's identity to the extension layer. Two approaches:

1. **Extension-data convention**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`.
2. **Dedicated sender field**: Add an `originSender` field to the swap call that the pool passes through to hooks, populated by the router from its own `msg.sender`.

Until one of these is implemented, the NatSpec for `SwapAllowlistExtension` and `MetricOmmSimpleRouter` must explicitly warn that allowlisting the router grants unrestricted swap access to all router callers.

---

### Proof of Concept

```
Setup:
  pool = pool with SwapAllowlistExtension (extension2, beforeSwap order = 2)
  alice = allowlisted user
  bob   = non-allowlisted user

Step 1 (admin):
  swapExtension.setAllowedToSwap(pool, alice,  true)   // intended allowlist
  swapExtension.setAllowedToSwap(pool, router, true)   // to let alice use the router

Step 2 (bob, unprivileged):
  router.exactInputSingle({
      pool:          pool,
      zeroForOne:    true,
      amountIn:      X,
      recipient:     bob,
      extensionData: ""
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives tokens

Result: bob swaps successfully despite never being allowlisted.
```

The `FullMetricExtension` integration test (`test_allowedSwapSucceeds`) only exercises direct pool calls via `TestCaller`, never a router-mediated path, so this bypass is untested in the existing suite. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
