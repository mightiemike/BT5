### Title
`SwapAllowlistExtension` gates the immediate pool caller (`sender`) rather than the true end-user, allowing any address to bypass per-user swap restrictions by routing through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the address that called `pool.swap()` — i.e., the immediate caller. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller, so `sender` = router address. If the pool admin allowlists the router (a natural configuration to support router-based swaps for their allowlisted users), every user on the network can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

**Root cause — wrong identity field checked in the guard:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument: [1](#0-0) 

The pool passes `msg.sender` of the `swap()` call as `sender`: [2](#0-1) 

Which flows through `ExtensionCalling._beforeSwap`: [3](#0-2) 

**The router is the immediate caller, not the user:**

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [4](#0-3) 

So the pool sees `msg.sender` = router address. The extension receives `sender` = router, and checks `allowedSwapper[pool][router]`. The actual end-user (`msg.sender` of the router call) is never inspected by the guard.

**The bypass scenario:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading to specific counterparties.
2. Pool admin allowlists specific users: `setAllowedToSwap(pool, user1, true)`.
3. Pool admin also allowlists the router: `setAllowedToSwap(pool, router, true)` — a natural step to allow those users to trade via the standard periphery.
4. Any arbitrary user (not in the allowlist) calls `router.exactInputSingle(pool, ...)`. The extension sees `sender` = router, finds `allowedSwapper[pool][router] = true`, and passes. The unauthorized user swaps successfully.

There is no way for the pool admin to allowlist specific users AND allow them to use the router without simultaneously opening the gate to all users, because the router address is a single shared public contract.

**Analog to PersonalAccountRegistry:**

In the external report, `_verifySender` checked `owners[sender].added` (set to `true` on add, never cleared on remove), so a removed owner still passed. Here, `beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the router (set to `true` to enable router access), so any user routing through the router passes — the guard checks the wrong identity field and cannot distinguish the actual end-user.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional partners, or regulatory compliance) can be fully bypassed by any user routing through the public `MetricOmmSimpleRouter`. The unauthorized user can execute swaps, extract value from the pool, and interact with pool liquidity in ways the pool admin explicitly intended to prevent. This breaks the core access-control invariant of the allowlist extension.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router address. This is a natural and expected configuration step for any pool that wants to support standard periphery usage for its allowlisted users — there is no other way to allow allowlisted users to use the router. Any pool that has both a `SwapAllowlistExtension` and the router allowlisted is fully exposed. The trigger is a normal, unprivileged user call to a public router function.

---

### Recommendation

The `SwapAllowlistExtension` must check the true end-user identity, not the immediate pool caller. Two approaches:

1. **Pass the original user through the router:** Modify `MetricOmmSimpleRouter` to encode `msg.sender` (the actual user) in `extensionData`, and modify `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.

2. **Check `sender` and fall back to `extensionData`:** In `beforeSwap`, if `sender` is a registered router, decode the real user from `extensionData` and check `allowedSwapper[pool][realUser]` instead.

3. **Require direct pool calls for allowlisted pools:** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and instead require allowlisted users to call `pool.swap()` directly. This is a UX limitation but avoids the bypass.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// user1 is NOT allowlisted
// user1 calls router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: user1,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    tokenIn: token1,
    deadline: block.timestamp,
    extensionData: ""
}));
// Extension sees sender = router (allowlisted), passes.
// user1 swaps successfully despite not being in the allowlist.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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
