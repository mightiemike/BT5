### Title
SwapAllowlistExtension Gates on Router Address Instead of End User — Allowlist Bypassed via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. Any admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the pool to every unprivileged user, fully defeating the allowlist.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no mechanism to forward the original end-user address: [4](#0-3) 

When the router is the caller, `sender = address(router)`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The end user's identity is permanently lost at the pool boundary.

**Two broken invariants result:**

1. **Allowlist bypass (primary impact):** To let legitimate allowlisted users swap through the standard periphery path, the admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every unprivileged user can call `router.exactInputSingle(pool, ...)` and the extension sees `sender = router` → allowed. The allowlist is completely bypassed.

2. **Broken UX for allowlisted users:** If the admin does *not* allowlist the router, allowlisted users who call through the router are rejected (`sender = router`, not in allowlist), even though their own address is allowlisted. They are forced to call `pool.swap()` directly, bypassing the standard periphery.

The `DepositAllowlistExtension` does not share this flaw because it gates on the `owner` argument (position recipient), not `sender`, so the adder path preserves the correct identity: [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker receives the same oracle-priced output as an allowlisted user, draining LP value from a pool that was designed to be closed. Loss is bounded only by pool liquidity and is repeatable across every affected pool.

### Likelihood Explanation

The router is the documented, supported periphery path for swaps. Any pool admin who deploys a curated pool and wants their allowlisted users to use the standard frontend/router will naturally allowlist the router address. The bypass requires no privileged access, no special token behavior, and no multi-step setup beyond a single `exactInputSingle` call through the public router.

### Recommendation

The extension must gate on the economically relevant actor, not the immediate pool caller. Two sound approaches:

1. **Pass end-user identity through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Check `sender` only for direct pool calls; require a signed or encoded user identity for router calls:** The extension inspects whether `sender` is a known router and, if so, decodes the real user from `extensionData`.

3. **Simplest fix — gate on `sender` and require direct pool calls for curated pools:** Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and revert if `sender` is a known router address.

The `DepositAllowlistExtension` pattern (gate on `owner`, not `sender`) is the correct model for the deposit side and should be mirrored on the swap side by having the router forward the end-user address explicitly.

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, router, true)   // to enable router-mediated swaps for alice
  admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution trace:
    router.exactInputSingle → pool.swap(recipient=bob, ...) [msg.sender = router]
    pool._beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap(sender=router, ...)
      → allowedSwapper[pool][router] == true  ✓ passes
    swap executes; bob receives output tokens

Result:
  bob, who is not allowlisted, successfully swaps on a curated pool.
  The allowlist check passed because it saw sender=router, not sender=bob.
``` [3](#0-2) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
