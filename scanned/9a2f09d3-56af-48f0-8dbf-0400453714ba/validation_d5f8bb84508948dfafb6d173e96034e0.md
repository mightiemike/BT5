### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address passed by the pool as `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users use the router), every unpermissioned user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Call chain:**

1. User (attacker) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` to the pool is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — passing the **router address** as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]`.

If the pool admin has allowlisted the router (so that their intended users can use the standard periphery), the check passes for **any caller** of the router, including completely unpermissioned addresses.

The pool's `swap()` function has no parameter for the originating user — it only exposes `msg.sender`: [1](#0-0) 

The extension receives and checks only that forwarded `msg.sender`: [2](#0-1) 

The router never forwards the originating EOA to the pool: [3](#0-2) 

Contrast this with `DepositAllowlistExtension`, which correctly gates on `owner` (an explicit parameter the adder passes through), not on `sender`: [4](#0-3) 

The swap path has no equivalent explicit-user parameter, so the extension is structurally unable to identify the real actor when the router is in the call stack.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the only way to let their approved users use the standard periphery) inadvertently opens the allowlist to every user of that router. Any unpermissioned address can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the public router and trade against the curated pool, bypassing the intended access control entirely. This constitutes a direct policy bypass on curated pools and, depending on pool design, can result in direct loss of LP principal (e.g., if the pool is intentionally restricted to a set of trusted counterparties to prevent adverse selection or front-running).

---

### Likelihood Explanation

- The router is the standard, documented periphery entry point for swaps.
- Pool admins who want their allowlisted users to use the router **must** allowlist the router address — there is no other supported path.
- The bypass requires no special privileges, no flash loans, and no unusual token behavior: a single public `exactInputSingle` call suffices.
- No existing test covers a router-mediated swap against a `SwapAllowlistExtension`-gated pool with a non-allowlisted EOA. [5](#0-4) 

The test suite only exercises direct pool calls (via `TestCaller`), never a router-mediated swap against the allowlist.

---

### Recommendation

The pool's `swap()` function should accept an explicit `originator` parameter (the actual user) that the router populates with `msg.sender` before calling the pool, and the pool should forward that value as `sender` to extension hooks. Alternatively, `SwapAllowlistExtension` should be redesigned to read the originating user from a trusted source (e.g., a transient-storage slot written by the router before calling the pool, analogous to how `MetricOmmSwapRouterBase` stores the payer). A simpler short-term mitigation is to document that the router address must never be allowlisted and that allowlisted users must call the pool directly — but this breaks the intended periphery UX.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
  admin calls setAllowedToSwap(pool, router, true)      // required so alice can use the router

Attack (attacker = any non-allowlisted EOA):
  attacker calls router.exactInputSingle({
      pool: pool,
      tokenIn: token0,
      tokenOut: token1,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  router calls pool.swap(recipient, true, X, limit, "", extensionData)
    → pool: msg.sender = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ← bypass succeeds
    → swap executes for attacker
```

The attacker receives token1 output from a pool that was intended to be restricted to `alice` only.

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
