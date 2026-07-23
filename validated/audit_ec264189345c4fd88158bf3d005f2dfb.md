### Title
SwapAllowlistExtension Bypass via Router — Any Unprivileged User Can Swap on Allowlist-Gated Pools Through MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps for legitimate users), every unprivileged user can bypass the per-user allowlist by routing through the same public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument: [1](#0-0) 

**Step 2 — Extension checks `sender` (the immediate pool caller) against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (whoever called `pool.swap()`) as the identity being gated: [2](#0-1) 

**Step 3 — Router calls `pool.swap()` directly, substituting itself as `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with no forwarding of the original `msg.sender`: [3](#0-2) 

The same pattern holds for `exactOutputSingle`, `exactInput`, and `exactOutput`. [4](#0-3) 

**Consequence — The allowlist checks the router address, not the end user.**

When any user calls the router, the extension evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the only way to let legitimate users swap via the router), the check passes for **every** caller regardless of their individual allowlist status.

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | Every user, including non-allowlisted ones, can bypass the guard |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users.

---

### Impact Explanation

The `SwapAllowlistExtension` is a production guard whose stated purpose is to restrict swaps to specific addresses per pool. A pool admin deploying this extension intends to control which counterparties can trade against the pool's liquidity. In oracle-based market-making pools, counterparty restriction is economically meaningful: it limits adverse selection, prevents uninformed flow from eroding LP value, and enforces bilateral agreements.

Once the router is allowlisted, any unprivileged user can execute swaps against the restricted pool. This exposes LP capital to uninformed or adversarial flow that the allowlist was designed to exclude, resulting in direct loss of LP principal through adverse selection on oracle-priced bins.

**Severity: Medium** — direct LP principal loss is contingent on the pool admin allowlisting the router, but that is the natural and expected action for any pool that intends to support router-mediated swaps.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router (the standard UX path) must allowlist the router address. This is a predictable, non-adversarial admin action. Once taken, the bypass is immediately available to all users with no further preconditions. No privileged access, no malicious setup, and no non-standard tokens are required.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **end user identity**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the router's encoding, which introduces its own trust assumptions.

2. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router address, and that allowlisted users must call `pool.swap()` directly. This is a usage restriction, not a code fix, and is fragile.

3. **Preferred — redesign the extension to check `tx.origin` or use a signed permit**: Neither is ideal on its own, but a signed permit pattern (user signs a swap authorization that the extension verifies) would correctly bind the allowlist check to the economic actor regardless of routing path.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  userA = allowlisted swapper (allowedSwapper[pool][userA] = true)
  userB = non-allowlisted swapper
  router = MetricOmmSimpleRouter (deployed, public)

Admin action (natural, non-adversarial):
  swapExtension.setAllowedToSwap(pool, address(router), true)
  // Admin allowlists the router so userA can use the standard UX

Attack:
  vm.prank(userB);
  router.exactInputSingle(ExactInputSingleParams({
      pool: address(pool),
      tokenIn: token0,
      ...
  }));
  // router calls pool.swap() → msg.sender to pool = router
  // extension checks allowedSwapper[pool][router] = true → PASSES
  // userB swaps successfully despite not being individually allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap executes, userB trades against LP liquidity
```

The root cause is in `SwapAllowlistExtension.beforeSwap` at the identity check: [5](#0-4) 

which evaluates the router address rather than the originating user whenever the router is the immediate caller of `pool.swap()`. [6](#0-5)

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
