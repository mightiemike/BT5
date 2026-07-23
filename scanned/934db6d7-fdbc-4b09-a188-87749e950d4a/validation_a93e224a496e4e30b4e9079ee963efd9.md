### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the allowlist entirely by calling the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` to the pool is the router, so the allowlist evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The actual user's identity is never checked.

The same structural problem exists for `exactOutputSingle`, `exactInput`, and `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses. To let those addresses use the standard router, the admin must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller of the router — including addresses that were never individually approved. Any unprivileged user can trade against the restricted pool by routing through `MetricOmmSimpleRouter`, defeating the access-control invariant entirely. The pool receives tokens in exchange, but the LP's intent — to trade only with vetted counterparties — is violated, exposing the pool to adverse selection from actors it explicitly excluded.

---

### Likelihood Explanation

The router is the canonical, publicly documented entry point for swaps. A pool admin who wants their allowlisted users to be able to use the router will naturally add the router to the allowlist. This is the expected operational path; no malicious setup is required. Any user who discovers the router is allowlisted can immediately exploit it. The existing test suite (`FullMetricExtension.t.sol`) only exercises direct pool calls through `TestCaller`, never the router path, so the bypass is not caught by tests. [6](#0-5) 

---

### Recommendation

The pool must forward the original caller's identity through the router so the extension can gate on the real user. Two complementary approaches:

1. **Router-side**: Add an optional `originator` field to swap parameters. The router stores `msg.sender` in transient storage before calling the pool and exposes it via a callback or passes it inside `extensionData`. The extension reads the originator from `extensionData` and checks that address instead of `sender`.

2. **Extension-side**: Document that `sender` is the immediate pool caller (router or EOA) and require pool admins to allowlist the router only when they intend to open access to all router users. Provide a separate `OriginatorSwapAllowlistExtension` that reads a caller-supplied originator from `extensionData` and verifies a signature or trusted-forwarder proof.

The simplest safe fix is to never allowlist the router as a blanket entry and instead require each user to call the pool directly, but this breaks the standard UX. The correct long-term fix is option 1.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — intending to let allowlisted users reach the pool via the router.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack
──────
4. attacker (not individually allowlisted) calls:
     router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls _beforeSwap(msg.sender=router, ...).
7. Extension evaluates:
     allowedSwapper[pool][router] == true  ✓  (passes)
8. Swap executes. attacker receives output tokens.
   The allowlist never checked attacker's address.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
``` [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
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
