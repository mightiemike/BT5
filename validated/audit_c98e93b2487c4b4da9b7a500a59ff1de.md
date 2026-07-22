### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the user. The extension therefore checks whether the router is allowlisted, not whether the actual user is. Any non-allowlisted user can bypass a curated pool's swap gate by routing through the public router.

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When the router calls `pool.swap()`, `sender` is the router address: [4](#0-3) 

The pool admin's intent is to gate individual users. The extension's check gates the router instead. This creates an irreconcilable dilemma:

- **Router not allowlisted:** allowlisted users cannot use the router at all.
- **Router allowlisted:** every user on the planet can bypass the per-user allowlist by routing through the public router.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool — the admin explicitly restricts which addresses may trade. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the curated pool and the extension will check `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the only way to let legitimate users use it), the guard is completely open to all users. This is a direct policy bypass enabling unauthorized swaps against LP capital in a pool that was designed to be permissioned.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the periphery. Any user who reads the interface will naturally use it. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — just calling the public router with the curated pool address.

### Recommendation

The extension must gate on the **original user**, not the intermediate router. Two sound approaches:

1. **Pass the original user through the router:** Have the router encode `msg.sender` in `extensionData` and have the extension decode and verify it. This requires a coordinated extension+router design.
2. **Check `recipient` instead of `sender`:** For swap allowlists the economically relevant actor is the recipient of output tokens. Alternatively, require direct pool calls for allowlisted pools (document that the router is incompatible with `SwapAllowlistExtension`).
3. **Preferred — add an `originalSender` field to the hook signature:** The pool could pass both `msg.sender` (the direct caller) and an optional `originalSender` that the router populates via `extensionData`, letting the extension choose which to gate.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to let legitimate users use router
  attacker = address not in allowedSwapper[pool]

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: curated_pool,
      recipient: attacker,
      ...
  })
  → router calls pool.swap(attacker, ...)
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes, attacker receives output tokens
  → allowlist completely bypassed
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
