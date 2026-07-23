### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end-user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on the public router, completely defeating the per-user allowlist.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the `sender` parameter in the ABI-encoded call to every configured extension: [2](#0-1) 

**Step 2 — The router is `msg.sender` to the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The router is therefore `msg.sender` inside the pool, so `sender` delivered to every `beforeSwap` hook is the **router address**, not the originating user: [3](#0-2) 

The same substitution occurs for `exactInput` (intermediate hops use `address(this)` = router), `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**Step 3 — The allowlist checks the substituted router address.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [5](#0-4) 

**Step 4 — The two broken outcomes.**

| Pool admin intent | What they configure | Actual result |
|---|---|---|
| Restrict to named users; allow router | Allowlist specific users + router address | Any user calls router → `sender` = router → passes → **full bypass** |
| Restrict to named users; no router | Allowlist specific users only | Named users call router → `sender` = router → **always reverts** (broken core flow) |

In the first case the allowlist is silently nullified. In the second case the pool's primary swap path is permanently broken for every allowlisted user.

---

### Impact Explanation

A restricted pool (e.g., institutional, KYC-gated, or whitelist-only) that allowlists the router to support normal UX exposes its full liquidity to any unprivileged caller. The attacker can drain token reserves through repeated swaps, causing direct LP principal loss. This matches the "broken core pool functionality causing loss of funds" and "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact gates.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the canonical swap entry point for end-users. Any pool admin who deploys a `SwapAllowlistExtension` and also wants users to be able to use the standard router will naturally allowlist the router address. The bypass is then immediately reachable by any unprivileged caller with no special setup.

---

### Recommendation

The extension must verify the **economic actor** (the originating user), not the intermediary. Two viable approaches:

1. **Extension-data attestation**: Require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and verify it. The extension should reject calls where `extensionData` is empty or the attested address is not allowlisted.

2. **Direct-call-only design**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by reverting when `sender != tx.origin` (acceptable only if the pool is not intended for contract callers).

The simplest production fix is approach (1): the router passes `abi.encode(msg.sender)` as part of `extensionData`, and the extension decodes and checks that address instead of the raw `sender` parameter.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, userA, true)       // allowlist one user
3. Pool admin calls setAllowedToSwap(pool, router, true)      // allowlist router for UX
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})
5. Router calls pool.swap(recipient, ...) — msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. userB's swap executes successfully in the restricted pool.
```

`userB` is never in `allowedSwapper` yet trades freely because the router's address satisfies the check. Repeating this drains LP reserves proportional to available liquidity. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-41)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

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
