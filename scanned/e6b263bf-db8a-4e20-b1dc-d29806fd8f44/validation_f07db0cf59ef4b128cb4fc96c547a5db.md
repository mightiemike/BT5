### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter: Router Address Replaces User Identity in Allowlist Check — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender` — the direct caller of `pool.swap()` — against the per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][original_user]`. A pool admin who allowlists the router to enable router-mediated swaps for their curated user set inadvertently opens the pool to every user who calls the router, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of `MetricOmmPool.swap()`. [1](#0-0) 

`MetricOmmPool.swap()` passes its own `msg.sender` as `sender` to the extension dispatcher:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` faithfully forwards that value as the `sender` argument to every configured extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
``` [4](#0-3) 

The pool's `msg.sender` is the router, so `sender = router` reaches the extension. The extension then checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (a natural step to enable router-mediated swaps for their curated users), the check passes for **any** caller of the router — not just the intended allowlisted users.

The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

---

### Impact Explanation

A curated pool's swap allowlist is completely bypassed for any user who routes through `MetricOmmSimpleRouter`. The admin cannot simultaneously (a) allow router-mediated swaps and (b) restrict swaps to specific users. Any non-allowlisted user can call `router.exactInputSingle()` and execute swaps on the curated pool, violating the admin-configured access boundary. This is an admin-boundary break: an unprivileged path (the public router) bypasses a configured guard.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who want to enable router-mediated swaps for their allowlisted users will naturally allowlist the router address. The bypass is then trivially reachable by any user with no special privileges. The `generate_scanned_questions.py` audit pivot explicitly flags this exact scenario:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [6](#0-5) 

---

### Recommendation

The `SwapAllowlistExtension` must check the original end-user identity, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.
2. **Check `sender` only for direct calls; reject router calls**: The extension can detect that `sender` is a known router and revert, forcing direct-only swaps on curated pools.
3. **Document the incompatibility**: If the design intent is that `sender` is always the direct caller, document clearly that allowlisting the router opens the pool to all router users, and that curated pools must not allowlist the router.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)   // allowlist alice
3. Admin calls setAllowedToSwap(pool, router, true)  // allowlist router so alice can use it
4. charlie (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(); pool sees msg.sender = router.
6. _beforeSwap passes sender = router to SwapAllowlistExtension.
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. charlie's swap executes on the curated pool, bypassing the allowlist.
```

The existing integration test `test_allowedSwapSucceeds` only tests direct-caller allowlisting and does not cover the router path, leaving this bypass untested. [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
