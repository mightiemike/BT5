### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the actual user's address. If the pool admin allowlists the router — a natural step to let allowlisted users access the router — every user, including those the admin intended to block, can bypass the allowlist by routing through the router.

---

### Finding Description

**Invariant broken:** The swap allowlist must gate the economic actor who initiates and benefits from the swap. Instead it gates the intermediary contract (`MetricOmmSimpleRouter`), which is shared by all users and enforces no per-user access control of its own.

**Exact call path:**

1. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(params)`. [1](#0-0) 

2. The router calls `pool.swap(...)` — `msg.sender` inside the pool is now the **router**.

3. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [2](#0-1) 

4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to the extension. [3](#0-2) 

5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. [4](#0-3) 

If the router is allowlisted, the check passes for **every caller of the router**, regardless of whether that caller is individually allowlisted.

**Why the admin allowlists the router:** A pool admin who restricts swaps to specific users (e.g., KYC-gated pool) still wants those users to be able to use the official router. The only mechanism available is `setAllowedToSwap(pool, router, true)`. That single entry opens the gate for all router users simultaneously, because the extension has no visibility into who called the router. [5](#0-4) 

The router forwards `extensionData` verbatim from the caller and adds no user-identity information, so the extension cannot recover the real user from the payload. [6](#0-5) 

**Contrast with `DepositAllowlistExtension`:** The deposit extension ignores the `sender` parameter and checks `owner` (the position owner), which the pool passes correctly regardless of who calls `addLiquidity`. The swap extension has no equivalent "owner" concept — it only has `sender`, which collapses to the router address for all router-mediated swaps. [7](#0-6) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (KYC, whitelist, institutional-only) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter` once the admin allowlists the router. The attacker receives real token output from the pool; the pool's LP providers bear the other side of every unauthorized swap. This is a direct loss of user principal and a broken core pool functionality (access-controlled swap flow).

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production extension explicitly designed for access-controlled pools.
- The `MetricOmmSimpleRouter` is the canonical user-facing entry point; allowlisted users will naturally want to use it.
- The only way to enable router access for allowlisted users is to allowlist the router itself, which is the exact action that opens the bypass.
- No special privilege or malicious setup is required beyond a standard admin configuration decision.

---

### Recommendation

The extension must gate the **actual user**, not the intermediary. Two viable approaches:

1. **Router-injected identity in `extensionData`:** Have `MetricOmmSimpleRouter` prepend `msg.sender` (the real user) to `extensionData` before forwarding to the pool. `SwapAllowlistExtension` decodes and checks that address. This requires the allowlist to trust the router as an honest forwarder, which is acceptable for a factory-deployed periphery contract.

2. **Separate allowlist entry for router-mediated swaps:** Document clearly that allowlisting the router grants access to all router users, and provide a companion extension that decodes a signed user credential from `extensionData` for router paths.

Either way, the current behavior — where `sender = router` for all router-mediated swaps — must be documented as a known limitation or fixed before the extension is used in access-controlled production pools.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension (extension1 = swapExt, beforeSwap order = 1).
2. Admin: swapExt.setAllowedToSwap(pool, userA, true)       // allowlist userA
3. Admin: swapExt.setAllowedToSwap(pool, router, true)      // enable router for userA
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: userB, ...})
5. router → pool.swap(recipient=userB, ...) with msg.sender=router
6. pool → swapExt.beforeSwap(sender=router, ...)
7. swapExt checks: allowedSwapper[pool][router] == true  ✓
8. Swap executes. userB receives token output. Allowlist bypassed.
``` [4](#0-3) [8](#0-7) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
