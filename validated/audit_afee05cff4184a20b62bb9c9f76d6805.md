### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit any router-mediated swap), every user — including those not on the allowlist — can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist: [1](#0-0) 

`msg.sender` inside the extension is the pool (the caller of the extension), used as the pool key. `sender` is the value the pool passes as the first argument to `_beforeSwap`, which is `msg.sender` at the pool's `swap` call site: [2](#0-1) 

(The `addLiquidity` function confirms the pattern: `_beforeAddLiquidity(msg.sender, owner, ...)` — `swap` follows the same convention.)

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At the pool, `msg.sender` is the router. The pool passes `router` as `sender` to `_beforeSwap`, and the extension checks `allowedSwapper[pool][router]`. The actual end user who called the router is never inspected.

This creates an inescapable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps blocked, even for allowlisted users |
| Yes | All users bypass the per-user allowlist via the router |

There is no configuration that simultaneously allows legitimate router use and enforces per-user allowlisting.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd traders, institutional counterparties) loses all access control for router-mediated swaps the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and trade against the pool, defeating the curation entirely. This is a direct policy bypass with fund-flow consequences: the pool receives tokens from and sends tokens to actors the admin explicitly intended to exclude.

---

### Likelihood Explanation

**Medium.** The bypass requires the pool admin to have allowlisted the router. This is a natural and expected operational step for any curated pool that also wants to support router-mediated swaps for its allowlisted users. The admin has no way to know that doing so opens the gate to everyone. The router is a public, permissionless contract, so once the router is allowlisted, the bypass is trivially reachable by any EOA.

---

### Recommendation

The extension must gate on the actual end user, not the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the original user through the router**: Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and check it. This requires a coordinated change to both the router and the extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the economically relevant actor is the recipient of output tokens. The extension already receives `recipient` as its second argument (currently ignored). Gating on `recipient` would correctly identify the beneficiary regardless of routing path — though this changes the semantic from "who initiates" to "who receives."

3. **Allowlist the router separately and require per-user proof in `extensionData`**: The extension can require a signed or otherwise verifiable user identity in `extensionData` when `sender` is a known router, falling back to direct `sender` checks otherwise.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=bob, ...)  →  msg.sender at pool = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes for bob despite bob not being on the allowlist

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
