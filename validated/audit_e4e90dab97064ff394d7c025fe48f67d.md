### Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end-user. If the pool admin allowlists the router to support router-based swaps, every user — including those explicitly not on the allowlist — can bypass the curated-pool gate.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the direct pool caller) is on the allowlist, using `msg.sender` (the pool) as the namespace key: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` with itself as `msg.sender`: [4](#0-3) 

The actual end-user (`msg.sender` of the router call) is stored only in transient storage for the payment callback and is **never forwarded to the extension**. The extension therefore sees `sender = address(router)`, not the real user.

---

### Impact Explanation

Two fund-impacting outcomes arise from this wrong-actor binding:

**Scenario A — Allowlist bypass (High):** The pool admin must allowlist the router address to let any user swap through the router. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][sender]` passes for every user who routes through the router, regardless of whether that user is individually permitted. The curated-pool invariant — only approved addresses may trade — is completely defeated for all router-mediated swaps.

**Scenario B — Broken core functionality (Medium):** If the admin allowlists individual user addresses (the intended design), those users cannot swap through the router because the extension sees the router address and reverts with `NotAllowedToSwap`. Allowlisted users are forced to call the pool directly, breaking the supported periphery path.

Both outcomes are contest-relevant: Scenario A is a direct policy bypass on curated pools; Scenario B breaks a core user flow.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for end-users. Any pool admin who deploys a `SwapAllowlistExtension` and wants users to swap through the router must allowlist the router, immediately triggering Scenario A. The trigger is a normal, expected admin action, not a malicious one.

---

### Recommendation

Pass the true end-user identity through the extension call chain. Two options:

1. **Preferred — add a `payer` / `originator` field to the extension interface.** The pool can read the originator from transient storage (already set by the router before calling `swap`) and pass it as an additional argument to `beforeSwap`. The extension then checks that address instead of `sender`.

2. **Minimal — document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps**, and require pools using this extension to be called directly. This does not fix Scenario A but at least prevents silent bypass.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin allowlists the router:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   (required so that any user can swap through the router)
3. Pool admin does NOT allowlist attacker:
       allowedSwapper[pool][attacker] == false

Attack
──────
4. Attacker calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool:      pool,
           recipient: attacker,
           ...
       })
5. Router calls pool.swap(recipient, ...) with msg.sender = router.
6. Pool calls extension.beforeSwap(router, ...).
7. Extension evaluates:
       allowAllSwappers[pool]          → false
       allowedSwapper[pool][router]    → true   ← router is allowlisted
   → check passes, swap executes.
8. Attacker receives output tokens from a pool they were never permitted to trade on.
```

The attacker is not on the allowlist; the router is. Because the extension checks the router address (`sender`) rather than the attacker's address, the guard fails open for every user who routes through `MetricOmmSimpleRouter`. [3](#0-2) [1](#0-0) [5](#0-4)

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
