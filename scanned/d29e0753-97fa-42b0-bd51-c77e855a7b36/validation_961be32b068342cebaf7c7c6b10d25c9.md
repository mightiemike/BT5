### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool binds to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the allowlist checks the router's address rather than the actual user. If the router is allowlisted for a curated pool, any unprivileged user bypasses the swap gate entirely by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with no mechanism to forward the original user's address: [4](#0-3) 

When a user calls `router.exactInputSingle()`, the call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap()          [msg.sender = router]
             → _beforeSwap(sender = router, ...)
                 → SwapAllowlistExtension.beforeSwap(sender = router)
                     → allowedSwapper[pool][router]  ← checks router, not user
```

The allowlist never sees the actual user's address. The existing integration test only exercises the direct-call path (via `TestCaller`), never the router path: [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address (a natural action to permit router-mediated swaps for approved users) inadvertently opens the gate to every user. Any address — including those explicitly not allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the curated pool and execute a swap that the allowlist was configured to block. The pool receives and settles the swap normally; LP funds are exposed to trades from actors the pool admin intended to exclude.

Even without the router being allowlisted, allowlisted users cannot use the router at all (the check fails on the router's address), breaking the expected swap UX for every curated pool.

---

### Likelihood Explanation

`SwapAllowlistExtension` is a production extension explicitly designed for curated pools. `MetricOmmSimpleRouter` is the canonical public swap entrypoint. A pool admin who wants to allow router-mediated swaps for their approved users will naturally allowlist the router, triggering the full bypass. The bypass requires no privileged access, no special token behavior, and no malicious setup — only a standard router call.

---

### Recommendation

The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Pool-level:** Add an explicit `sender` parameter to `pool.swap()` so periphery contracts can forward the originating user address. The pool should validate this parameter (e.g., require it equals `msg.sender` when called directly, or accept it from trusted routers).

2. **Extension-level (short-term):** `SwapAllowlistExtension.beforeSwap` should check `recipient` as a proxy for the beneficiary, or the extension should be documented as incompatible with router-mediated flows until the pool-level fix lands.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so approved users can swap via router
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Admin does NOT allowlist attacker
// allowedSwapper[pool][attacker] == false

// Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    tokenOut:        address(token1),
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    recipient:       attacker,
    deadline:        type(uint256).max,
    priceLimitX64:   0,
    extensionData:   ""
}));
// Swap succeeds: pool.swap() sees msg.sender = router (allowlisted)
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
// Attacker receives output tokens despite not being on the allowlist
```

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
