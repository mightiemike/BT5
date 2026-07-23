### Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any Caller to Bypass the Per-User Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. A pool admin who allowlists the router so that their permitted users can trade through it inadvertently opens the pool to every caller of the router, completely defeating the allowlist guard.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself the pool's `msg.sender`: [4](#0-3) 

The result is a structural identity mismatch:

| Swap path | `sender` seen by extension | Allowlist entry needed |
|---|---|---|
| User → Pool directly | `user` | `allowedSwapper[pool][user]` |
| User → Router → Pool | `router` | `allowedSwapper[pool][router]` |

A pool admin who wants to restrict swaps to a specific set of users and also allow those users to use the router must allowlist the router address. But `allowedSwapper[pool][router] = true` grants every caller of the router permission to swap, because the extension has no way to distinguish which end user initiated the router call. The per-user allowlist is completely bypassed for all router-mediated swaps.

---

### Impact Explanation

Any unprivileged user can swap on a pool that is supposed to be restricted to a curated allowlist by routing through `MetricOmmSimpleRouter`. This allows unauthorized traders to:

- Execute swaps against LP liquidity that was deposited under the assumption that only vetted counterparties could trade.
- Drain token reserves from restricted pools (e.g., institutional or KYC-gated pools) at oracle-derived prices, causing direct loss of LP principal.

The impact is **High**: direct loss of LP assets from pools whose access control is entirely defeated.

---

### Likelihood Explanation

The likelihood is **High**:

1. `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it.
2. The bypass requires no special privileges, no malicious setup, and no non-standard tokens.
3. A pool admin who deploys a restricted pool and wants their allowlisted users to have a good UX will naturally allowlist the router, triggering the vulnerability without realizing it.
4. The `SwapAllowlistExtension` interface (`setAllowedToSwap`, `isAllowedToSwap`) gives no indication that router-mediated swaps are treated differently, so the misconfiguration is easy to make.

---

### Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediate router. Two complementary fixes:

1. **Pass the originating user through `extensionData`**: The router should encode `msg.sender` (the end user) into `extensionData` and the extension should decode and check it. This requires a coordinated change in the router and extension.

2. **Check `tx.origin` as a fallback** (weaker, but simpler): Replace the `sender` check with `tx.origin` when `sender` is a known router. This is fragile but avoids the router-identity problem.

The cleanest fix is option 1: `MetricOmmSimpleRouter` encodes the real caller into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the only permitted trader
  - Pool admin calls setAllowedToSwap(pool, router, true)      // needed so alice can use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
        pool=restrictedPool, tokenIn=..., amountIn=..., ...
    )
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks: allowedSwapper[pool][router] == true  → PASSES
  - Bob's swap executes against LP liquidity he was never supposed to access.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; bob drains LP funds at oracle price.
``` [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
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
