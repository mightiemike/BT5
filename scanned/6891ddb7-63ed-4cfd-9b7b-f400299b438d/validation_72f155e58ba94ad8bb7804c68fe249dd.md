### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on a pool to a configured set of addresses. However, `MetricOmmPool.swap` always passes `msg.sender` (the direct caller of the pool) as the `sender` argument to the `beforeSwap` hook. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address — not the actual user's address. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for their permitted users), every unpermissioned user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool always binds `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` hardcoded as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

There is no `sender` parameter in `pool.swap`'s public signature — the pool has no way to receive the original user's address from the router.

**Step 2 — Router calls `pool.swap` as itself.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly; the pool's `msg.sender` is therefore the router contract: [3](#0-2) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 3 — `SwapAllowlistExtension` checks the wrong actor.**

The extension gates on `sender` (the value the pool passed), keyed by `msg.sender` (the pool): [4](#0-3) 

When a swap arrives through the router, `sender = router_address`. The check becomes `allowedSwapper[pool][router]`. The actual user's address is never consulted.

**Step 4 — The bypass.**

A pool admin who wants to allow specific users to swap (e.g., KYC'd counterparties) and also wants those users to be able to use the standard router must allowlist the router:

```
swapAllowlist.setAllowedToSwap(pool, address(router), true);
```

Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every call that arrives through the router, regardless of who the actual `msg.sender` of the router call is. Any unpermissioned user can now call `router.exactInputSingle(...)` and the extension passes.

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to restrict trading to a curated set of addresses. Once the router is allowlisted (the only way to let permitted users trade through the standard periphery), the restriction is completely nullified for all router-mediated paths. Any user can execute swaps on the restricted pool, receiving pool output tokens at oracle-derived prices. Because the pool's price is set by an external oracle and LP funds are finite, unrestricted swaps can drain LP positions or extract value at prices the pool admin did not intend to expose to the general public. This matches the "allowlist bypass → direct loss of LP assets" impact class.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. This is not a hypothetical configuration: any pool admin who deploys a `SwapAllowlistExtension` and also wants their permitted users to access the pool through the standard `MetricOmmSimpleRouter` must allowlist the router. The two goals — per-user restriction and router support — are mutually exclusive with the current design, but a pool admin who does not understand the actor-binding flaw will naturally allowlist the router, triggering the bypass. The trigger is a single `setAllowedToSwap(pool, router, true)` call by the pool admin, which is an expected operational step.

---

### Recommendation

The pool's `swap` function should accept an explicit `sender` parameter (the economically relevant actor) that the router populates with `msg.sender` before forwarding to the pool. Alternatively, `SwapAllowlistExtension` should read the original user from a trusted forwarding field in `extensionData` when `sender` is a known router, or the router should be redesigned to pass the original caller through a verified channel (e.g., transient storage read by the extension via a shared interface). The simplest fix consistent with the existing architecture is to add a `sender` field to `pool.swap`'s signature so the router can pass `msg.sender` explicitly, and the pool can validate it against `msg.sender` (i.e., require `sender == msg.sender || isApprovedRouter(msg.sender)`).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted.
// Pool admin also allowlists the router so alice can use it.
swapAllowlist.setAllowedToSwap(pool, alice, true);
swapAllowlist.setAllowedToSwap(pool, address(router), true); // required for alice to use router

// Attack: bob (not allowlisted) calls the router directly.
// pool.swap receives msg.sender = router → extension checks allowedSwapper[pool][router] = true → passes.
vm.prank(bob);
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
// bob receives token1 from the restricted pool — allowlist completely bypassed.
```

The root cause is the same wrong-actor binding class as the `bridge_tokens` report: the identity checked by the guard (`router`) is not the identity the protocol intends to gate (`bob`), because the pool has no mechanism to distinguish the two when the router is the direct caller. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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
