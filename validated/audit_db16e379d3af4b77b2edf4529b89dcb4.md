### Title
SwapAllowlistExtension Gates Router Address Instead of End-User, Allowing Complete Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool always sets `sender = msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end-user. If the router is allowlisted to enable router-based swaps, every user on the network can bypass the allowlist entirely.

---

### Finding Description

**Pool passes `msg.sender` (the router) as `sender` to the extension:**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` parameter to every configured extension: [2](#0-1) 

**Extension checks the router address, not the actual user:**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded — the router: [3](#0-2) 

**Router calls `pool.swap()` directly, making itself the `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with no mechanism to forward the original caller's identity: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Contrast with the deposit path (correctly designed):**

`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` — the position owner explicitly passed by the caller and forwarded by the pool — not `sender`. This correctly identifies the economic actor regardless of whether `MetricOmmPoolLiquidityAdder` is used as an intermediary: [5](#0-4) 

The swap path has no equivalent explicit "actual swapper" parameter — only `sender` (immediate caller) and `recipient` (output destination). Neither reliably identifies the end-user through a router.

---

### Impact Explanation

Two fund-impacting failure modes exist:

**Mode 1 — Complete allowlist bypass (critical):** A pool admin who wants to support router-based swaps must allowlist the router address. Once `allowedSwapper[pool][router] = true`, every user on the network can call `router.exactInputSingle(...)` and the extension passes unconditionally. The allowlist provides zero access control. Any user excluded from the allowlist (e.g., sanctioned addresses, non-KYC'd users, competitors) can freely swap against the pool.

**Mode 2 — Broken core swap flow for legitimate users (high):** If the admin does not allowlist the router, every allowlisted user who attempts to swap through the router receives `NotAllowedToSwap`. The only path that works is calling `pool.swap()` directly, which requires the caller to implement `IMetricOmmSwapCallback` for token settlement — not a realistic expectation for end-users. The pool's swap functionality is effectively unusable through the standard periphery.

Both modes represent broken core pool functionality with direct fund impact: either unauthorized parties drain liquidity, or authorized LPs cannot generate fee revenue because the swap path is blocked.

---

### Likelihood Explanation

**High.** Every pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` hits one of the two failure modes deterministically. The router is the standard user-facing entry point documented in the protocol. A pool admin who configures an allowlist and also wants router support will naturally allowlist the router, triggering Mode 1. No special attacker capability is required — any EOA calling the router suffices.

---

### Recommendation

The swap path needs an explicit "actual swapper" identity that survives router intermediation. Two viable approaches:

1. **Extend the swap signature with an explicit `swapper` parameter** (analogous to `owner` in `addLiquidity`). The pool passes it to extensions as the gated identity. The router forwards `msg.sender` as `swapper`. This mirrors the deposit path's correct design.

2. **Encode the actual user in `extensionData` and have the extension decode it**, with the router injecting `msg.sender` before forwarding. This avoids a core interface change but requires the extension to trust the router's encoding, which introduces its own trust assumptions.

Option 1 is structurally cleaner and consistent with how `DepositAllowlistExtension` correctly gates `owner`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls: extension.setAllowedToSwap(pool, router, true)
    → intent: allow router-mediated swaps for allowlisted users
    → actual effect: allowedSwapper[pool][router] = true

Attack:
  attacker (not individually allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

  router calls:
    pool.swap(attacker, zeroForOne, amount, limit, "", extensionData)
    // msg.sender to pool = router

  pool calls:
    _beforeSwap(router, attacker, ...)

  extension evaluates:
    allowedSwapper[pool][router] → true  ✓ passes

  result:
    attacker swaps successfully against the allowlisted pool
    allowlist provides zero protection
```

The `SwapAllowlistExtension` unit tests confirm the check is on `sender` (the pool's `msg.sender`), and the integration test in `FullMetricExtensionTest` only tests direct pool calls — the router-mediated bypass path is untested. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L32-38)
```text
  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
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
