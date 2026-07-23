### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the actual user. The allowlist therefore gates the router's address, not the individual swapper's identity. Any user who routes through the router is checked as if they were the router, making the per-user allowlist trivially bypassable.

---

### Finding Description

**Call chain:**

```
User (EOA) → MetricOmmSimpleRouter.exactInputSingle()
           → pool.swap(recipient, ...) [msg.sender = router]
           → ExtensionCalling._beforeSwap(msg.sender=router, ...)
           → SwapAllowlistExtension.beforeSwap(sender=router, ...)
           → allowedSwapper[pool][router] checked — NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When the user enters through `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly — making the router the `msg.sender` to the pool: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The allowlist check therefore resolves to `allowedSwapper[pool][routerAddress]`, not `allowedSwapper[pool][actualUser]`.

**The impossible choice for pool admins:**

- If the admin does **not** allowlist the router: no EOA user can swap through the router (even allowlisted ones), because EOAs cannot implement `IMetricOmmSwapCallback` and must use the router.
- If the admin **does** allowlist the router (to let their allowlisted users trade): **every user on-chain** can bypass the allowlist by routing through the same public router.

There is no configuration of `SwapAllowlistExtension` that simultaneously (a) allows allowlisted EOA users to swap via the router and (b) blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) loses all access control the moment any allowlisted user needs to use `MetricOmmSimpleRouter`. The admin must allowlist the router, which opens the pool to all users. Non-allowlisted users can then execute swaps against LP positions, extracting value at oracle-quoted prices that the pool's LP providers did not consent to offer to arbitrary counterparties. This is a direct loss of LP principal and a broken core pool invariant (curated access).

---

### Likelihood Explanation

Likelihood is **high**. EOAs cannot call `pool.swap()` directly because the pool immediately calls back `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` — an EOA has no code to handle this callback and the transaction reverts. `MetricOmmSimpleRouter` is the only supported periphery path for EOA swappers. Any pool that deploys `SwapAllowlistExtension` and expects EOA users to trade is structurally forced into the bypass condition. No special attacker setup is required beyond calling the public router.

---

### Recommendation

The `beforeSwap` hook must receive and check the **original user's address**, not the intermediary router's address. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData`, and the extension decodes and checks it. This requires the extension to trust the encoding, which is fragile.

2. **Preferred — check `recipient` or add a dedicated `originator` field**: Redesign the `IMetricOmmExtensions.beforeSwap` interface to include an explicit `originator` address (the end-user) distinct from `sender` (the immediate caller). The pool populates `originator` from a trusted source (e.g., the router stores it in transient storage and the pool reads it, similar to how `_getPayer()` works in the router's callback context).

3. **Immediate mitigation**: Document that `SwapAllowlistExtension` gates the direct pool caller, not the end-user, and that it must not be used with shared router contracts. Pools requiring per-user gating must require users to call the pool through individual per-user proxy contracts that are themselves allowlisted.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// allowedSwapper[pool][allowedUser] = true
// allowedSwapper[pool][router]      = false  (router not allowlisted)
// allowedSwapper[pool][attacker]    = false  (attacker not allowlisted)

// Step 1: Admin allowlists the router so allowedUser can trade via periphery.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Now: allowedSwapper[pool][router] = true

// Step 2: Attacker (not allowlisted) calls the router.
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Pool calls _beforeSwap(sender=router, ...)
// Extension checks allowedSwapper[pool][router] == true → PASSES
// Attacker swaps successfully despite not being allowlisted.
```

The `SwapAllowlistExtension.beforeSwap` check at line 37 resolves to `allowedSwapper[pool][router]`, which is `true`, and the attacker's swap executes against LP funds at oracle price without restriction. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
