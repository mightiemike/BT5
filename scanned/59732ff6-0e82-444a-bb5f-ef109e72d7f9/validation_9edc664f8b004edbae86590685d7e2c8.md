### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the per-pool swap allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees the router as `sender`, not the original user. If the pool admin allowlists the router to enable router-mediated swaps for their approved users, every unprivileged user can bypass the allowlist by routing through the same public router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first parameter — the caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` with itself as `msg.sender`: [4](#0-3) 

The allowlist therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (broken UX).
- **Allowlist the router** → every unprivileged user can call `router.exactInputSingle` and pass the allowlist check, because the router is the `sender` the extension sees.

The same bypass applies to `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`.

`DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` parameter (the position owner), which the liquidity adder passes correctly as the actual depositor: [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or protocol-internal actors) can be fully bypassed by any unprivileged user the moment the pool admin allowlists the router. The attacker receives real token output from the pool's LP reserves, directly harming LPs whose capital is now exposed to unrestricted trading they did not consent to. This is a direct loss-of-principal-exposure impact on LP assets and a broken core pool invariant (curated access control).

### Likelihood Explanation

The trigger is a single, operationally natural admin action: allowlisting the router so that approved users can use the standard periphery. Any pool that (a) uses `SwapAllowlistExtension` and (b) wants its approved users to access the router will hit this path. The attacker needs no special privilege — only a public router call.

### Recommendation

The `sender` identity passed to `beforeSwap` must represent the economic actor, not the intermediary. Two viable fixes:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData`; the extension decodes and checks that address. This requires a convention between the router and the extension.
2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the economically relevant actor; however this shifts the check to the output side and may not match all use cases.
3. **Separate the allowlist into a direct-call list and a router-mediated list**: Allow the admin to configure which addresses may call the pool directly vs. which may use the router, with the router enforcing its own per-user check before calling the pool.

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  admin calls setAllowedToSwap(pool, router, true)  // so alice can use the router

Attack (bob, not allowlisted):
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(bob, zeroForOne, amount, ...)  [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives token output from LP reserves
```

Bob, who was never allowlisted, successfully extracts value from a pool that was supposed to be restricted to alice and other approved counterparties. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-41)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
