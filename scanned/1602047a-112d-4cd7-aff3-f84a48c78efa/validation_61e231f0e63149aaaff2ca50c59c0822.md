### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating user's address. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the gate to every user on the network, completely defeating the per-user allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, so `sender = router_address` is what reaches the extension. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```
allowedSwapper[pool][router]
```

instead of the intended:

```
allowedSwapper[pool][actual_user]
``` [3](#0-2) 

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Allowlist only specific EOAs | Allowlisted users **cannot** use the router (broken functionality) |
| Allowlist the router address | **Every** user can bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. The extension's identity check is structurally misbound to the intermediary, not the economic actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, specific market makers, or institutional LPs). Once the pool admin allowlists the router — a natural step to let legitimate users access the pool through the standard periphery — any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` targeting that pool and swap successfully. The extension's `onlyPool` guard confirms the caller is a valid pool, but the identity it gates (`sender`) is the router, not the user.

Unauthorized swappers can drain LP principal by executing swaps the pool admin explicitly intended to block. This is a direct loss of LP assets and a complete break of the admin-configured access boundary. [4](#0-3) 

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard user-facing swap entry point. Any pool admin who deploys a swap-allowlisted pool and wants legitimate users to access it through the router will allowlist the router address. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a call to the public router. The attack is repeatable on every block. [5](#0-4) 

---

### Recommendation

The extension must gate the originating user, not the intermediary. Two viable approaches:

1. **Trusted forwarder pattern**: The router encodes `msg.sender` into `extensionData` using a well-known ABI layout, and the extension decodes and verifies it only when `sender` is a known trusted router (verified against the factory registry). Non-router direct calls continue to use `sender` directly.

2. **Explicit swapper field**: Add a `swapper` parameter to `pool.swap` (separate from `msg.sender`) that the router populates with `msg.sender` before forwarding. The extension checks `swapper` instead of `sender`.

Either approach must ensure the identity field cannot be spoofed by an untrusted caller.

---

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin allowlists Alice and the router (to let Alice use the router)
ext.setAllowedToSwap(pool, alice, true);
ext.setAllowedToSwap(pool, address(router), true); // <-- necessary for Alice to use router

// Attack: Bob (not allowlisted) calls the router directly
// ext.beforeSwap receives sender = address(router)
// allowedSwapper[pool][router] == true  →  check passes
// Bob's swap executes against the restricted pool

vm.prank(bob); // bob is NOT in the allowlist
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Bob successfully swaps; allowlist is bypassed
```

The root cause is in `SwapAllowlistExtension.beforeSwap` checking `sender` (the router) rather than the originating user, and in `MetricOmmPool.swap` passing `msg.sender` (the router) as `sender` to the extension hook. [6](#0-5) [3](#0-2) [5](#0-4)

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
