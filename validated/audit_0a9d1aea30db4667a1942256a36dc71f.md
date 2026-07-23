### Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the end user. The extension therefore checks the router's allowlist status, not the actual swapper's. This creates a two-sided failure: allowlisted users are silently blocked from using the router, and if the admin allowlists the router to restore router access, every user — including non-allowlisted ones — bypasses the gate.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle(params)
         → pool.swap(params.recipient, ...)          // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  // checks router, not user
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router never forwards the original caller's address — it calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

The allowlist is keyed and administered per-user: [5](#0-4) 

---

### Impact Explanation

**Phase 1 — Broken functionality for allowlisted users.** A pool admin allowlists Alice (`allowedSwapper[pool][alice] = true`). Alice calls `router.exactInputSingle(...)`. The extension sees `sender = router`, looks up `allowedSwapper[pool][router]` = `false`, and reverts with `NotAllowedToSwap`. Alice cannot use any router function (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) on the curated pool, even though she is explicitly permitted.

**Phase 2 — Complete allowlist bypass.** The admin, observing that allowlisted users cannot use the router, adds the router to the allowlist (`allowedSwapper[pool][router] = true`). Now `allowedSwapper[pool][router]` = `true` for every call that arrives through the router. Non-allowlisted Bob calls `router.exactInputSingle(...)`. The extension sees `sender = router`, checks `allowedSwapper[pool][router]` = `true`, and passes. Bob trades on a pool that was supposed to be restricted to approved counterparties only. The curation policy is completely nullified for all router-mediated swaps.

The impact is broken core pool functionality (the allowlist extension is the primary access-control mechanism for curated pools) and potential direct loss to LPs who deposited under the assumption that only vetted counterparties would trade against them.

---

### Likelihood Explanation

The path from Phase 1 to Phase 2 is a natural administrative response. The admin observes that allowlisted users cannot use the router and adds the router address to the allowlist to restore usability. This is the obvious fix and requires no attacker sophistication — any user who discovers the router is allowlisted can exploit it. The router is a public, documented periphery contract, so its address is known.

---

### Recommendation

The pool should pass the original end-user identity to the extension, not `msg.sender`. Two complementary fixes:

1. **In `MetricOmmPool.swap`**: Accept an explicit `swapper` parameter (the actual end user) and pass it as `sender` to `_beforeSwap`, separate from the callback payer (`msg.sender`). The router would supply `msg.sender` (the user) as `swapper`.

2. **In `SwapAllowlistExtension.beforeSwap`**: Until the pool interface is updated, document that the extension is incompatible with router-mediated swaps and that allowlisting the router address opens the gate to all users.

Alternatively, mirror the pattern used by `DepositAllowlistExtension` for the deposit path: the pool's `addLiquidity` accepts an explicit `owner` parameter that is the economically relevant actor, decoupled from `msg.sender`. The swap path should expose an equivalent explicit `swapper` field.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists alice directly
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Alice cannot use the router — blocked because sender = router
vm.prank(alice);
// This reverts with NotAllowedToSwap even though alice is allowlisted
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: alice,
    ...
}));

// Admin "fixes" this by allowlisting the router
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Now non-allowlisted Bob bypasses the gate through the router
vm.prank(bob); // bob is NOT in the allowlist
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    ...
}));
// Succeeds — allowlist completely bypassed
```

The root cause is at: [1](#0-0) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-29)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
