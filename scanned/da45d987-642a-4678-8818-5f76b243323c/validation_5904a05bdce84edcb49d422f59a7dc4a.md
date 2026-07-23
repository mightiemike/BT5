### Title
`SwapAllowlistExtension` checks the router's address instead of the actual swapper when swaps are routed through `MetricOmmSimpleRouter`, allowing complete allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to gate swap access to specific addresses on a per-pool basis. However, the hook checks the `sender` argument forwarded by the pool, which is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to enable router-based swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Step 1 — How the pool forwards `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the hook call: [2](#0-1) 

**Step 2 — What the extension actually checks.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the pool's `msg.sender`) and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Step 3 — How the router calls the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly. The pool's `msg.sender` is therefore the router address, not the originating user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Step 4 — The dilemma this creates for pool admins.**

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who wants allowlisted users to be able to use the router (the standard UX path) must call `setAllowedToSwap(pool, router, true)`. The moment they do, the check becomes `allowedSwapper[pool][router] == true` for every caller who routes through the router — including every address that was never individually permitted. [6](#0-5) 

The `DepositAllowlistExtension` does **not** share this flaw: it checks the `owner` parameter (the LP position owner), which the liquidity adder passes explicitly and which the pool does not replace with `msg.sender`. [7](#0-6) 

---

### Impact Explanation

A curated pool that relies on `SwapAllowlistExtension` to restrict trading to specific counterparties loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. An unauthorized user can execute swaps at the pool's oracle-anchored bid/ask prices, draining LP-owned token balances at rates the pool admin never intended to offer. Because the pool's pricing is oracle-driven and not self-correcting, repeated unauthorized swaps in one direction can exhaust one side of the pool's liquidity, causing direct loss of LP principal.

---

### Likelihood Explanation

The router is a public, permissionless contract. Any user can call it. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration step for any pool that wants to support the standard periphery UX. The bypass requires no special privileges, no flash loans, and no multi-step setup.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **originating user**, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the originating user's address in `extensionData` and have the extension decode and check that address. The extension should revert if the field is absent or zero.
2. **Direct-pool-only enforcement**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and add an explicit check that `sender` is not a known router address, or that `sender == tx.origin` (with the usual caveats about smart-contract callers).

The simplest safe fix is option 1: the router already accepts per-hop `extensionData` and forwards it unchanged to the pool, which forwards it to the extension.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use the standard UX: `setAllowedToSwap(pool, router, true)`.
4. Bob (never allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. The router calls `pool.swap(bob, ...)` — pool's `msg.sender` is the router.
6. The pool calls `extension.beforeSwap(router, bob, ...)` — extension's `sender` is the router.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes without revert.
8. Bob's swap executes at the oracle price, draining pool liquidity the admin intended to reserve for Alice only. [3](#0-2) [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
