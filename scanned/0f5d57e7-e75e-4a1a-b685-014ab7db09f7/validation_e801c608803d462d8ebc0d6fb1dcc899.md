### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the originating user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle(...)
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` (the router) as the `sender` argument to the extension hook:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks the router address against the allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool, sender = router — the actual user is never checked
```

The pool admin who wants to allow router-mediated swaps for legitimate users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, **any** user — including those never individually allowlisted — can call any of the router's public entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and the extension check passes unconditionally.

The `MetricOmmSimpleRouter` is a public, permissionless contract with no caller restrictions of its own:

```solidity
// MetricOmmSimpleRouter.sol L67-86
function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
  _checkDeadline(params.deadline);
  ...
  IMetricOmmPoolActions(params.pool).swap(params.recipient, ...);
  ...
}
```

The unit tests for `SwapAllowlistExtension` only exercise the extension in isolation (pool calls extension directly), never through the router, so the mismatch is untested:

```solidity
// SwapAllowlistSubExtension.t.sol L26-30
function test_revertsWhenSwapperNotAllowed() public {
  vm.prank(address(pool));
  vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
  extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
}
```

The integration test in `FullMetricExtension.t.sol` allowlists `callers[0]` (a `TestCaller` that calls the pool directly), not the router, so the bypass path is never exercised there either.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or institutional participants) is completely unprotected once the router is allowlisted. Any address can execute swaps of arbitrary size against the pool's liquidity, draining LP value at oracle-anchored prices without the pool admin's consent. Because the router is a shared public contract, a single allowlist entry for the router opens the gate to the entire internet.

---

### Likelihood Explanation

The scenario is highly likely in practice:
1. Pool admins who want to support the standard periphery UX **must** allowlist the router — there is no other way to let legitimate users trade through the router.
2. The bypass requires no special privileges, no custom contract, and no unusual token behavior — any EOA can call `exactInputSingle` directly.
3. The mismatch is invisible from the admin's perspective: `isAllowedToSwap(pool, router)` returns `true` as intended, but the invariant "only allowlisted users can swap" is silently broken.

---

### Recommendation

The extension must check the **originating user**, not the immediate pool caller. The `recipient` field is also router-controlled and unsuitable. The correct fix is to pass the actual end-user identity through `extensionData` and have the router populate it, or — more robustly — have the pool expose a separate `originalSender` field that the router sets in transient storage before calling `swap`, analogous to how `MetricOmmSwapRouterBase` already stores the payer in transient storage for the callback.

A minimal fix within the current interface: require that any allowlisted intermediary (router) encodes the real user address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known intermediary. A cleaner fix is to add an `originalSender` slot to the router's transient context and expose it via a view function that the extension can call back into.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap hook
  pool admin calls: swapExtension.setAllowedToSwap(pool, address(router), true)
    (to allow legitimate users to trade via the router)

Attack:
  attacker = address not in allowedSwapper[pool]
  attacker calls:
    router.exactInputSingle({
      pool: pool,
      recipient: attacker,
      zeroForOne: true,
      amountIn: X,
      ...
    })

  Execution:
    router → pool.swap(attacker, true, X, ...)   // msg.sender = router
    pool   → _beforeSwap(sender=router, ...)
    ext    → allowedSwapper[pool][router] == true  ← passes
    swap executes, attacker receives output tokens

Result:
  attacker swaps successfully despite never being individually allowlisted.
  The allowlist guard is completely bypassed for all router-mediated swaps.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
