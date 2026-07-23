### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the end user. A pool admin who allowlists the router address to enable standard periphery access inadvertently opens the gate to every user, completely defeating the per-user curation the extension was deployed to enforce.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, zeroForOne, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]     // ← wrong actor
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly: [1](#0-0) 

The pool passes its own `msg.sender` (the router) as the `sender` argument to every extension hook: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [4](#0-3) 

The allowlist is keyed `allowedSwapper[pool][swapper]` and populated by the pool admin per individual address: [5](#0-4) 

**The dilemma this creates for pool admins:**

| Admin action | Result |
|---|---|
| Allowlist individual users only (not the router) | Allowlisted users **cannot** use the router; they must call the pool directly |
| Allowlist the router address | **Every** user can bypass the per-user gate by routing through the router |
| Set `allowAllSwappers = true` | Gate is fully open — extension is inert |

There is no configuration that simultaneously allows router-based swaps and enforces per-user access control.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise vetted addresses loses that protection entirely the moment the router is allowlisted. Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) and trade against the pool's liquidity. LP funds are exposed to counterparties the pool admin explicitly intended to exclude, and the pool's curation invariant — the primary reason for deploying the extension — is silently broken.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point for swaps. Pool admins who deploy `SwapAllowlistExtension` and also want their allowlisted users to access the standard router UX will naturally allowlist the router address. The bypass requires no special knowledge: any user who observes the router is allowlisted can call it directly. The trigger is a routine, well-intentioned admin action, not an exotic attack.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediary contract. Two sound approaches:

1. **Forward the original caller in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Check `recipient` as a proxy for the user**: For single-hop swaps the recipient is typically the user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router-aware allowlist**: The extension reads the original payer from a router-set transient storage slot (similar to how `MetricOmmSimpleRouter` already stores `_getPayer()` in transient storage for callback settlement), and gates on that address instead of `sender`.

The cleanest fix is option 1 or 3, ensuring the checked identity is always the address that economically benefits from the swap.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool:
//   - SwapAllowlistExtension deployed and wired as beforeSwap hook
//   - Only `allowedUser` is allowlisted
//   - Router is also allowlisted so allowedUser can use the standard UI

swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // ← enables router

// Attacker (not allowlisted) bypasses the gate:
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         token1,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        amountIn:        1000,
        amountOutMinimum: 0,
        zeroForOne:      false,
        priceLimitX64:   type(uint128).max,
        extensionData:   ""
    })
);
// ✓ swap succeeds — extension checked allowedSwapper[pool][router] == true
// ✓ attacker receives token0 output from the curated pool
```

The pool's `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so `SwapAllowlistExtension` passes. The attacker's address is never consulted. [4](#0-3) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
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
