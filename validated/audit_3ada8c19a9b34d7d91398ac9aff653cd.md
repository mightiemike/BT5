The call chain is fully traceable. Here is the analysis:

**Full call chain:**

1. `MetricOmmSimpleRouter::exactInputSingle` — `msg.sender` = user
2. Router calls `IMetricOmmPoolActions(pool).swap(...)` — pool sees `msg.sender` = **router**
3. `MetricOmmPool::swap` calls `_beforeSwap(msg.sender, ...)` — passes **router** as `sender`
4. `ExtensionCalling::_beforeSwap` encodes `sender` = router and calls the extension
5. `SwapAllowlistExtension::beforeSwap(sender=router, ...)` executes the guard:

```solidity
// msg.sender here is the pool; sender is the router
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert NotAllowedToSwap();
}
```

The hook checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

**The identity confusion is structural and permanent.** The pool passes its own `msg.sender` (the router) as `sender` to the extension. There is no mechanism to recover the original EOA through this path.

---

### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` — Any User Can Swap Through an Allowlisted Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps on `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `MetricOmmPool::swap`. When `MetricOmmSimpleRouter` intermediates, `sender` is the router's address, not the original user's address. If the pool admin allowlists the router (the only way to permit any router-mediated swap for legitimate users), the allowlist is completely bypassed for all users.

### Finding Description

The `SwapAllowlistExtension` is designed to restrict which addresses may swap in a given pool. The check in `beforeSwap` is:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument passed by the pool, which is the pool's own `msg.sender` — i.e., whoever called `MetricOmmPool::swap`. [1](#0-0) 

In `MetricOmmPool::swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling::_beforeSwap` forwards this value unchanged to the extension: [3](#0-2) 

When `MetricOmmSimpleRouter::exactInputSingle` (or any `exact*` function) calls the pool, the pool's `msg.sender` is the **router contract**, not the original user: [4](#0-3) 

Therefore the hook evaluates `allowedSwapper[pool][router]`. The pool admin faces an impossible choice:
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through it.
- **Do not allowlist the router** → every legitimately allowlisted user is blocked from using the router.

### Impact Explanation

If the pool admin allowlists the router to enable router-mediated swaps for legitimate users (the expected operational path), any unprivileged attacker can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router targeting the restricted pool and the `beforeSwap` guard will pass. The allowlist — the pool's primary loss-prevention control — is fully neutralized. This constitutes unauthorized access to a restricted pool and broken core functionality of the extension guard.

### Likelihood Explanation

The router is a standard, publicly deployed periphery contract. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps must allowlist the router, making the bypass trivially reachable by any user with no preconditions, no special tokens, and no privileged access.

### Recommendation

The `sender` passed to the extension must represent the **original user**, not the intermediary. Two approaches:

1. **Pass `tx.origin` as an additional field** — fragile and generally discouraged.
2. **Require the router to forward the original caller** — add an `originSender` parameter to `swap` (or use `extensionData`) so the router can attest the original `msg.sender`, and have the extension verify it. The pool can then check `allowedSwapper[pool][originSender]` instead of `allowedSwapper[pool][router]`.
3. **Require direct pool interaction for allowlisted pools** — document and enforce that pools using `SwapAllowlistExtension` must not allowlist any router, and users must call the pool directly.

Option 2 is the most robust: the router already has `msg.sender` at entry and can pass it through `extensionData`; the extension can decode and verify it.

### Proof of Concept

**Setup:**
- Pool P uses `SwapAllowlistExtension`.
- Pool admin calls `setAllowedToSwap(P, router, true)` to allow router-mediated swaps for legitimate users.
- Attacker (address `A`) is NOT in `allowedSwapper[P]`.

**Transaction 1 (attacker):**
```solidity
router.exactInputSingle(ExactInputSingleParams({
    pool: P,
    recipient: attacker,
    tokenIn: token0,
    amountIn: X,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
```

**Execution trace:**
1. Router calls `P.swap(attacker, true, X, 0, "", "")` — pool sees `msg.sender = router`.
2. Pool calls `_beforeSwap(router, ...)`.
3. Extension checks `allowedSwapper[P][router]` → **true** → guard passes.
4. Attacker receives output tokens from a pool they were never authorized to access.

The note about "two transactions" and "stale threshold state" in the question is not necessary — the bypass is complete in a single transaction. The `SwapAllowlistExtension` has no threshold or observation state; the identity confusion alone is sufficient. [5](#0-4) [6](#0-5)

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
