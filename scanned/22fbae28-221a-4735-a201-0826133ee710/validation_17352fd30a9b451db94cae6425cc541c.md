### Title
SwapAllowlistExtension Checks Router Address Instead of User — Allowlist Fully Bypassed via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the extension checks the router's address — not the user's. Any pool admin who allowlists the router (required for router-mediated swaps to work at all) inadvertently grants every public user the ability to bypass the per-user allowlist.

### Finding Description

**Root cause — wrong actor in the allowlist key**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap`. When the user goes through the router, `sender` = router address.

**Router call path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no user-identity forwarding: [4](#0-3) 

The pool's `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The dilemma this creates for pool admins**

| Router allowlist state | Effect |
|---|---|
| Router **not** in `allowedSwapper` | All router-mediated swaps revert — even for individually allowlisted users. Router is unusable on this pool. |
| Router **is** in `allowedSwapper` | Every public user can call `exactInputSingle` and bypass the per-user allowlist entirely. |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

### Impact Explanation

Any user not on the allowlist can execute swaps on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle`. The pool admin's intent — restricting trading to a vetted set of addresses — is completely defeated. Depending on the pool's purpose (e.g., institutional-only liquidity, KYC-gated pools, rate-limited market-making pools), this allows arbitrary users to drain LP value at oracle-anchored prices the pool was not designed to offer them.

### Likelihood Explanation

The router is the standard, documented periphery entry point. Any user who reads the periphery README or inspects the router contract can discover this path. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The bypass is unconditional once the router is allowlisted.

### Recommendation

The extension must check the **economic actor** — the end user — not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the original user through the router.** Add a `swapper` field to the swap callback data or use a transient-storage context (similar to how the router already stores `_setNextCallbackContext`) so the extension can recover the true initiator.

2. **Check `recipient` instead of `sender` in the allowlist.** If the pool's design intent is to gate who *receives* output, `recipient` (already passed to `beforeSwap`) is router-independent. If the intent is to gate who *initiates* the trade, option 1 is required.

3. **Document that the router is incompatible with `SwapAllowlistExtension`** and enforce this at the factory level by reverting pool creation that pairs the two.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured (allowAllSwappers = false)
  - allowedSwapper[pool][alice] = true   (alice is the only allowed swapper)
  - allowedSwapper[pool][router] = true  (admin must do this for router to work)

Attack (executed by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, ..., recipient: bob})
  2. Router calls pool.swap(recipient=bob, ...) — router is msg.sender
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true → swap proceeds
  5. bob receives output tokens despite never being on the allowlist

Result: bob bypasses the curated allowlist and executes a swap the pool admin
        intended to block, receiving oracle-priced output at LP expense.
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
