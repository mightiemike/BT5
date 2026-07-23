### Title
SwapAllowlistExtension checks router address instead of actual swapper, enabling allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the router is allowlisted (a natural admin action to enable router-mediated swaps), any unprivileged user can bypass the per-user allowlist entirely.

### Finding Description

**Step 1 — Extension checks `sender`, which is the immediate pool caller.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded from the pool: [1](#0-0) 

**Step 2 — Pool forwards its own `msg.sender` as `sender`.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, so `sender` in the extension equals whoever called `pool.swap()`: [2](#0-1) 

**Step 3 — Router calls `pool.swap()` without forwarding the actual user.**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly. The pool sees `msg.sender = router`. The actual user (`msg.sender` of the router call) is stored only in transient callback context for payment, never forwarded to the pool as `sender`: [3](#0-2) 

**Step 4 — The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.**

The check-identity mismatch is structural: the extension was designed to gate individual users, but the pool's `sender` argument is the immediate caller, which is the router for all router-mediated swaps. There is no mechanism in the extension or the router to recover the actual user's identity — `extensionData` is user-controlled bytes that the extension ignores.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a set of known addresses.
2. Admin allowlists the router so that allowlisted users can use it: `extension.setAllowedToSwap(pool, router, true)`.
3. Any non-allowlisted user calls `router.exactInputSingle(...)`. The pool calls `_beforeSwap(router, ...)`. The extension evaluates `allowedSwapper[pool][router] == true` → passes. The swap executes.

The allowlist is completely bypassed for every user who routes through the router.

**Secondary broken-functionality path (no bypass needed):**

If the admin does *not* allowlist the router, then allowlisted users who try to use the router are blocked (`allowedSwapper[pool][router] == false`), even though they are individually permitted. The extension makes it impossible to simultaneously allow specific users and allow those users to use the router.

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to restrict swap access to a curated set of addresses (e.g., KYC'd counterparties, institutional LPs, or protocol-controlled addresses). The bypass allows any unprivileged user to swap in the restricted pool, potentially:

- Extracting value at favorable oracle-driven prices that were reserved for allowlisted participants.
- Draining pool liquidity that LPs deposited under the assumption of restricted access.
- Violating regulatory or contractual access-control requirements baked into the pool's design.

This is a direct loss-of-principal risk for LPs and a broken core pool invariant (the allowlist guard).

### Likelihood Explanation

The bypass is triggered whenever the admin allowlists the router — a natural and expected configuration step for any pool that wants allowlisted users to be able to use the standard periphery router. The admin has no way to achieve the intended goal (allow specific users via the router) without also opening the pool to all users. The attacker needs no special privileges, no tokens beyond the swap input, and no prior setup.

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economically relevant actor, not the immediate pool caller. Two concrete fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension` decode and verify it (requires a trusted router check or a signed payload).
2. **Extension-side**: If `sender` is a known router address, read the actual payer from a standardized field in `extensionData` rather than trusting `sender` directly.

Either way, the extension must be able to distinguish "router acting on behalf of user X" from "user X acting directly."

### Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Admin allowlists only alice for direct swaps
ext.setAllowedToSwap(pool, alice, true);
// Admin also allowlists the router so alice can use it
ext.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Succeeds: extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][bob]
``` [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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
