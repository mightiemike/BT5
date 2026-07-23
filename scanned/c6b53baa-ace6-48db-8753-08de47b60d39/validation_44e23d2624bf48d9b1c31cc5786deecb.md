### Title
SwapAllowlistExtension Checks Immediate Pool Caller Instead of Actual User, Enabling Router-Mediated Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the immediate `msg.sender` of `pool.swap`. When `MetricOmmSimpleRouter` mediates a swap, `sender` is the router address, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps, every non-allowlisted user can bypass the per-user gate by routing through the router in a single transaction.

---

### Finding Description

**Root cause — wrong identity checked in the extension:**

`SwapAllowlistExtension.beforeSwap` receives `sender` and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` here is the pool (correct), and `sender` is whatever the pool passed as the first argument to `_beforeSwap`.

**The pool always passes its own `msg.sender` as `sender`:**

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- sender = immediate caller of pool.swap
    recipient,
    ...
);
``` [2](#0-1) 

**The router calls `pool.swap` directly, making itself `msg.sender`:**

```solidity
// MetricOmmSimpleRouter.sol L71-80
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
``` [3](#0-2) 

When `exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is called, the pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The bypass path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` wired into `beforeSwap`.
2. Admin allowlists specific users: `setAllowedToSwap(pool, userA, true)`.
3. Admin also allowlists the router to support router-mediated swaps for those users: `setAllowedToSwap(pool, router, true)`.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(...)` — pool passes `sender = router` to `_beforeSwap`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `userB` successfully swaps, bypassing the per-user allowlist entirely.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` because all four entry points call `pool.swap` with `msg.sender = router`. [4](#0-3) 

The actual user who initiated the transaction (`msg.sender` of the router call) is stored only in the router's transient callback context for payment purposes and is never forwarded to the pool or the extension.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting which addresses may trade against a pool's LP positions. Once the router is allowlisted (a natural admin action to support UX), the guard is completely neutralized for all router-mediated swaps. Any address — including bots, competitors, or adversarial traders — can execute swaps against the pool's liquidity at oracle-derived prices. For pools where the allowlist is the primary protection against adverse selection or unauthorized access, this results in direct loss of LP principal through unauthorized trades.

---

### Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural and expected configuration step: a pool admin who wants allowlisted users to benefit from the router's multi-hop or exact-output routing will allowlist the router. The admin has no in-protocol signal that doing so opens the gate to all users — the extension's name ("SwapAllowlistExtension"), its NatSpec ("Gates `swap` by swapper address"), and its `isAllowedToSwap` view all suggest per-user gating, not per-caller gating. The mistake is therefore likely for any operator who deploys a permissioned pool and also wants to support the periphery router.

---

### Recommendation

The extension must gate the actual originating user, not the immediate caller of `pool.swap`. Two concrete options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to not forge the identity, which is acceptable for a protocol-owned router.

2. **Separate router-level allowlist from pool-level allowlist**: The router enforces its own per-user allowlist before calling the pool, and the pool-level extension only allowlists the router as a trusted intermediary. This keeps the two concerns separate and auditable.

Either way, the extension's `beforeSwap` must not treat the router address as a sufficient identity for per-user access control.

---

### Proof of Concept

```solidity
// Scenario: pool has SwapAllowlistExtension; admin allowlists router to support UX.
// userB is NOT individually allowlisted but bypasses the guard via the router.

// Admin setup (legitimate):
swapExt.setAllowedToSwap(address(pool), address(router), true);  // allow router
swapExt.setAllowedToSwap(address(pool), userA, true);            // allow userA directly

// Attack (userB, not allowlisted):
vm.prank(userB);
// userB calls the router — router calls pool.swap with msg.sender = router
// extension checks allowedSwapper[pool][router] == true → passes
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    recipient:       userB,
    tokenIn:         token0,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   0,
    deadline:        block.timestamp + 1,
    extensionData:   ""
}));
// swap succeeds — userB drained LP liquidity despite not being allowlisted
``` [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
