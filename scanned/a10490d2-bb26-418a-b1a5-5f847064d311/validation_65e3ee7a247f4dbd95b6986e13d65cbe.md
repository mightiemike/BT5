### Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the pool forwards the router's address as `sender` to the extension. The extension then checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin adds the router to the allowlist (required for any router-mediated swap to succeed), every user — including non-allowlisted ones — can bypass the restriction by routing through the public router.

---

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — the router is `msg.sender` to the pool.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the **router address**. [1](#0-0) 

4. `ExtensionCalling._beforeSwap` encodes that router address as the `sender` argument and dispatches to the extension. [2](#0-1) 

5. `SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`msg.sender` here is the pool; `sender` is the router. The actual end-user's address is never consulted.

**The dilemma this creates for pool admins:**

| Router in allowlist? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (legitimate users can't use router) | Blocked |
| Yes | Allowed | **Also allowed — bypass** |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) instead of calling `pool.swap` directly. If the pool admin has added the router to the allowlist to support normal router-mediated trading, the allowlist provides zero protection: every address on the network can swap in the restricted pool. This breaks the core access-control invariant of `SwapAllowlistExtension` and allows unauthorized parties to execute swaps — draining liquidity at oracle-anchored prices, front-running allowlisted LPs, or trading in pools intended for KYC'd or institutional participants only.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point. Users are expected to interact with pools through it. Any pool that uses `SwapAllowlistExtension` and also needs to support router-mediated swaps (the common case) must add the router to the allowlist, at which point the bypass is trivially reachable by any address with no special privileges or setup.

---

### Recommendation

The extension must identify the **economic actor** (the end-user), not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router or a signed payload.
2. **Check `sender` only for direct pool calls; require the router to forward the real user**: Add a `swapFor(address realUser, ...)` entry point on the router that encodes the real user in `extensionData`, and have the extension verify the router's signature or identity before trusting the forwarded address.
3. **Gate at the router level**: Deploy a router wrapper that enforces the allowlist before calling the pool, so the pool-level extension only needs to trust the wrapper.

The simplest safe fix is to not allowlist the router at all and require allowlisted users to call `pool.swap` directly — but this must be explicitly documented and enforced, as it breaks the standard UX.

---

### Proof of Concept

```
Setup:
  - Pool P uses SwapAllowlistExtension E.
  - Pool admin calls E.setAllowedToSwap(P, router, true)   // needed for any router swap
  - Pool admin calls E.setAllowedToSwap(P, alice, true)    // alice is the intended allowlisted user
  - bob is NOT in the allowlist.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender to pool = router
  3. Pool calls E.beforeSwap(router, recipient, ...)
  4. Extension checks: allowedSwapper[P][router] == true  → passes
  5. Swap executes for bob with no restriction.

Result: bob swaps in a pool he was explicitly excluded from.
``` [4](#0-3) [5](#0-4) [6](#0-5)

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
