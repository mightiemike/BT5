### Title
`SwapAllowlistExtension` Allowlist Bypassed Completely When Swapping Through `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the caller of `pool.swap()`, so the extension checks whether the **router** is allowlisted — not the actual end-user. If the pool admin allowlists the router (required for any legitimate user to use it), every unprivileged user can bypass the per-user allowlist by routing through the public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other `exact*` entry point) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice:

- **Allowlist the router** → every unprivileged user can bypass the per-user gate by routing through the public router.
- **Do not allowlist the router** → every legitimately allowlisted user is blocked from using the supported periphery path.

Either branch breaks the allowlist invariant.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional traders, or any other curated set) provides zero on-chain enforcement once the router is allowlisted. Any address can call `MetricOmmSimpleRouter.exactInputSingle` with the pool address and execute a swap that the pool admin explicitly intended to block. This constitutes a complete admin-boundary break: an unprivileged path (the public router) bypasses the configured access control, allowing unauthorized users to trade against LP assets under conditions the pool admin did not sanction.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap interface for end-users. Any user who discovers the bypass — or simply uses the router as intended — triggers it. No special privileges, flash loans, or multi-step setup are required. The trigger is a single public call to `exactInputSingle` or any other `exact*` function on the router.

### Recommendation

The `sender` identity checked by `SwapAllowlistExtension` must be the actual end-user, not the intermediary. Two sound approaches:

1. **Pass the original user through the router**: Add a `swapper` parameter to `pool.swap()` (separate from `msg.sender`) that the router populates with `msg.sender` before forwarding. The extension then checks that field instead of the pool's `msg.sender`.
2. **Check `msg.sender` at the extension level**: Have the extension check `msg.sender` of the pool call (the router) only as a secondary gate, and require the router to attest the real user via a signed payload in `extensionData`. The extension verifies the attestation.

Until fixed, pool admins should be warned that `SwapAllowlistExtension` provides no protection for swaps routed through `MetricOmmSimpleRouter`.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to allow legitimate users
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: attacker,
      ...
  })

  Router calls pool.swap(recipient, ...) with msg.sender = router
  Pool calls _beforeSwap(msg.sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true → passes
  Swap executes for attacker despite attacker not being on the allowlist
```

The `FullMetricExtensionTest` in `metric-periphery/test/extensions/FullMetricExtension.t.sol` tests direct pool calls only and never exercises the router path, so the bypass is untested: [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L155-175)
```text
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
