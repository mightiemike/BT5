Looking at the key extension files, the pool's swap dispatch, and the router's call path in parallel to trace the exact identity that each guard checks.

**Key observation in `SwapAllowlistExtension.beforeSwap`:**

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` = pool (the pool calls the extension). `sender` = whoever called `pool.swap()`.

**In `MetricOmmPool.swap()`:**
```solidity
_beforeSwap(msg.sender, recipient, ...);  // msg.sender = router when routed
```

**In `MetricOmmSimpleRouter.exactInputSingle()`:**
```solidity
IMetricOmmPoolActions(params.pool).swap(params.recipient, ...);
// pool sees msg.sender = router → sender = router forwarded to extension
```

The extension checks `allowedSwapper[pool][router]`, not the actual end-user.

---

### Title
SwapAllowlistExtension Allowlist Bypassed via Router When Router Address Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool sees `sender = router`. A pool admin who allowlists the router to enable router-mediated swaps inadvertently allows every unprivileged user to bypass the allowlist, because the extension cannot distinguish individual users behind the router.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards it verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` variant) is called, the router calls `pool.swap()` directly: [4](#0-3) 

Inside the pool, `msg.sender = router`, so `sender = router` is what the extension sees. The extension then evaluates `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to trade via the router **must** allowlist the router address. But once the router is allowlisted, `allowedSwapper[pool][router]` passes for **every** user who routes through the router, regardless of whether that user is individually allowlisted. The extension has no visibility into the actual end-user behind the router call. The router stores the real payer in transient storage for the callback: [5](#0-4) 

but never exposes it to the pool or the extension.

### Impact Explanation
Any unprivileged user can bypass the swap allowlist on a pool that has allowlisted the router address. The pool's access control is completely broken for router-mediated swaps. Unauthorized users can execute swaps against a pool intended to be restricted (e.g., KYC-gated, protocol-internal, or institutional-only pools), draining liquidity or extracting value from participants who deposited under the assumption that only allowlisted counterparties could trade against them.

### Likelihood Explanation
Medium. A pool admin who deploys a `SwapAllowlistExtension` and also wants to support the standard router workflow will naturally call `setAllowedToSwap(pool, router, true)`. This is the expected operational pattern for any pool that intends to be accessible via the periphery router. The bypass is then immediately available to any user with no special privileges or setup required.

### Recommendation
The `SwapAllowlistExtension` must gate by the actual end-user, not the immediate caller. Two viable approaches:

1. **`extensionData` forwarding**: Require the router to encode the real user's address in `extensionData`; the extension decodes and verifies it. The pool admin allowlists individual users, not the router.
2. **Trusted-forwarder pattern**: Maintain a separate set of trusted intermediaries (e.g., the router) that are permitted to forward swaps on behalf of individually allowlisted users, with the actual user identity passed through `extensionData` and verified by the extension.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router to enable router-mediated swaps.
3. Pool admin calls `setAllowedToSwap(pool, userA, true)` — allowlisting user A individually.
4. User B (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — pool sees `msg.sender = router`.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` = `true` → **PASSES**.
7. User B successfully swaps against the restricted pool, bypassing the allowlist entirely. [6](#0-5) [7](#0-6)

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
