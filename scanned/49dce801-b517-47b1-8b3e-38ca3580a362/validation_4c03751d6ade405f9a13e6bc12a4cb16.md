### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Allowlist via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router to enable router-based swaps, every user — including non-allowlisted ones — can bypass the per-user restriction by calling the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router the `msg.sender` of that call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router address — not the actual end user: [3](#0-2) 

The allowlist is keyed `allowedSwapper[pool][swapper]`. When the router calls the pool, the lookup becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router address (necessary to support any router-based swap), the check passes for **every** end user, regardless of whether that user is individually allowlisted.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers) cannot enforce that restriction for router-based swaps. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade against the pool's LP reserves, bypassing the intended access control. This breaks the core pool invariant that "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it." [4](#0-3) 

### Likelihood Explanation

The pool admin must allowlist the router to enable router-based swaps at all. Any pool that supports both the `SwapAllowlistExtension` and the `MetricOmmSimpleRouter` is affected. The bypass requires only a standard router call — no special permissions, no flash loans, no malicious setup.

### Recommendation

Pass the original end-user address through the swap path so the extension can check it. One approach: store the initiating `msg.sender` in transient storage inside the router before calling `pool.swap`, and expose a `swapInitiator()` view that extensions can query. Alternatively, redesign `beforeSwap` to accept an explicit `initiator` field distinct from `sender`, populated by the pool from a router-supplied callback context.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured on `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists `userA` directly: `setAllowedToSwap(pool, userA, true)`.
3. Pool admin also allowlists the router to support router-based swaps: `setAllowedToSwap(pool, router, true)`.
4. `userB` (not individually allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
6. Pool calls `extension.beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
7. Swap executes successfully for `userB`, bypassing the per-user allowlist. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
