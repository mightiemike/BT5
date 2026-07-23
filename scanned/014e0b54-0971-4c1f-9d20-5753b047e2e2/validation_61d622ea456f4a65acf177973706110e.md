Looking at the `SwapAllowlistExtension` and how `sender` flows from the router through the pool into the extension hook, there is a valid analog.

**Tracing the actor binding:**

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap(...)`, the pool's `msg.sender` is the **router address**, not the end user: [4](#0-3) 

So the allowlist check becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (a natural step to enable router-based trading for a curated pool), the check passes for **every user** who routes through it, regardless of whether that user is individually allowlisted.

---

### Title
SwapAllowlistExtension gates on the router address instead of the actual end-user, allowing any user to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate caller of `MetricOmmPool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router's address. If the router is allowlisted for a pool — a natural configuration for enabling router-based trading — any user can bypass the per-user allowlist and trade in a curated pool.

### Finding Description
The actor identity passed to the extension hook is the **immediate caller of the pool**, not the originating user. The chain is:

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — pool sees `msg.sender = router`.
3. Pool calls `_beforeSwap(msg.sender /*= router*/, recipient, ...)`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to the extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]`.

If the pool admin has executed `setAllowedToSwap(pool, router, true)` — intending to allow router-based trading for their allowlisted users — the check passes for **every** user who calls through the router, including users who are not individually allowlisted.

The extension has no mechanism to distinguish between different end-users when they share the same intermediary (the router). The allowlist mapping is keyed on `pool → swapper`, and the swapper identity collapses to the router address for all router-mediated trades. [3](#0-2) [5](#0-4) [6](#0-5) 

### Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., trusted market makers, KYC'd counterparties, or institutional traders) is fully bypassed for any user routing through `MetricOmmSimpleRouter` once the router is allowlisted. Unauthorized users can trade in the pool, causing LP losses due to adverse selection if the pool's curation policy was designed to prevent uninvited flow. The pool's core protection invariant — "only allowlisted addresses may swap" — is broken.

### Likelihood Explanation
Medium. Pool admins who want to allow router-based trading for their curated pool would naturally allowlist the router address. The extension design and documentation do not warn that allowlisting the router collapses all per-user distinctions. The router is a core periphery contract expected to be used with pools, making this a realistic misconfiguration.

### Recommendation
The `beforeSwap` hook must check the actual end-user identity, not the immediate pool caller. Options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Separate `sender` from `payer`**: The pool's `swap()` interface could accept an explicit `swapper` address (the economic actor) distinct from `msg.sender` (the technical caller), and the extension checks the explicit `swapper`.
3. **Document the limitation clearly**: If the current design is intentional, document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and provide no per-user router path.

### Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists only address Alice: setAllowedToSwap(pool, alice, true).
3. Pool admin also allowlists the router (to let Alice use the router):
       setAllowedToSwap(pool, router, true).
4. Unauthorized user Bob calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...}).
5. Pool calls extension.beforeSwap(router, bob, ...).
6. Check: allowedSwapper[pool][router] == true  →  passes.
7. Bob's swap executes in the curated pool despite not being individually allowlisted.
8. LPs suffer adverse selection from Bob's uninvited flow.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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
