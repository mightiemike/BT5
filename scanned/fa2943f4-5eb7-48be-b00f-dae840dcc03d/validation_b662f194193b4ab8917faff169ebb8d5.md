### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end user, allowing any user to bypass the swap allowlist by routing through `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking `sender`, which is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for allowlisted users inadvertently grants every user the ability to bypass the allowlist, because the extension cannot distinguish between different end users going through the same router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the pool's own `swap()` call:

```solidity
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  recipient,
  ...
)
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

So `msg.sender` to the pool is the **router address**, and that router address is what `SwapAllowlistExtension` checks against the allowlist — not the actual end user.

**The asymmetry with `DepositAllowlistExtension` exposes the design flaw.** The deposit allowlist correctly checks `owner` (the position owner explicitly threaded through the call chain), not `sender`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

The swap interface has no equivalent separate "actual user" field — `sender` is the only identity the extension receives, and it collapses to the router for all router-mediated swaps.

---

### Impact Explanation

A pool admin who configures a curated pool (only specific counterparties may trade) and also wants allowlisted users to be able to use the supported `MetricOmmSimpleRouter` must allowlist the router address. The moment `allowedSwapper[pool][router] = true`, **every user** — including non-allowlisted ones — can bypass the allowlist by routing through the router. LPs on a curated pool who expected only trusted counterparties are exposed to unrestricted oracle-priced trading, which can drain LP principal through adversarial or uninformed order flow the pool was specifically designed to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, publicly documented swap entrypoint. A pool admin who sets up a swap allowlist and wants allowlisted users to access the router will naturally add the router to the allowlist — this is the only way to make router-mediated swaps work for allowlisted users. The bypass is therefore a predictable consequence of a reasonable and expected admin configuration, not an exotic edge case. [5](#0-4) 

---

### Recommendation

The swap hook interface should expose the actual end user's identity separately from `sender`, mirroring how the liquidity interface exposes `owner` independently of `sender`. Until then, `SwapAllowlistExtension` must document that allowlisting the router grants unrestricted access to all router users, and pool admins must be warned never to allowlist the router on a curated pool. A more robust fix is to have the router forward the original caller's address through `extensionData` and have the extension decode and verify it — though this requires a coordinated change across the router and extension.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in the `beforeSwap` order.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, tokenIn: ..., zeroForOne: true, amountIn: X, ...})`.
5. Router calls `pool.swap(recipient, true, X, priceLimit, "", extensionData)` — `msg.sender` to the pool is the router.
6. Pool calls `_beforeSwap(msg.sender=router, recipient, ...)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` = `true` → does **not** revert.
8. Bob's swap executes on the curated pool, bypassing the allowlist entirely. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-41)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
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
