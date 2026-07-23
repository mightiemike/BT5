### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the original user. The extension therefore checks the router's address against the allowlist, not the actual swapper's address. Any user can bypass a curated pool's swap allowlist by routing through the public router.

---

### Finding Description

**Pool → Extension argument binding**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with its own `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

**Extension allowlist check**

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist: [3](#0-2) 

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)` directly. The pool's `msg.sender` is therefore the **router**, not the original user: [4](#0-3) 

**Result**: the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

**Two broken scenarios**

| Pool admin intent | What happens |
|---|---|
| Allowlist the router so users can swap through it | Every user — including non-allowlisted ones — passes the check; allowlist is completely bypassed |
| Allowlist individual user addresses | Those users cannot use the router at all; their swaps revert because the router is not allowlisted |

Both outcomes break the invariant that "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."

The `DepositAllowlistExtension` does **not** share this flaw — it checks `owner` (the position owner explicitly passed to `addLiquidity`), which the liquidity adder preserves correctly: [5](#0-4) 

---

### Impact Explanation

**High** — direct policy bypass on curated pools. If the pool admin allowlists the router (the natural setup for a public periphery), any unprivileged user can swap on a pool that was intended to be restricted. This breaks the core access-control invariant of the extension and allows unauthorized trading, which can drain LP value on pools designed for permissioned participants.

---

### Likelihood Explanation

**High** — `MetricOmmSimpleRouter` is the primary supported swap entry point for EOA users. Any user aware of the router can exploit this without any special setup. The existing test suite only exercises the direct-pool path through `TestCaller`, so the bypass is untested: [6](#0-5) 

---

### Recommendation

The extension must gate the **original user**, not the intermediary contract. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `sender` at the router level before calling the pool**: The router reads the allowlist and reverts before forwarding the call, keeping the extension as the authoritative gate for direct pool calls.

The pool admin documentation should also explicitly warn that allowlisting the router grants access to all router users.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   (natural setup: "let users swap through the router")
3. Non-allowlisted attacker calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Router calls pool.swap(attacker, ...) — pool's msg.sender = router.
5. _beforeSwap passes sender = router to the extension.
6. Extension checks allowedSwapper[pool][router] → true → swap proceeds.
7. Attacker successfully swaps on a pool intended to be restricted.
```

Conversely, if the admin allowlists individual users instead of the router, those users' `router.exactInputSingle` calls revert because `allowedSwapper[pool][router]` is false — breaking the expected user flow. [7](#0-6) [8](#0-7)

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
