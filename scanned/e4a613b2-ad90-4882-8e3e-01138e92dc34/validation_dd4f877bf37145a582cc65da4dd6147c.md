### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Swap Allowlist via Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the pool call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router address (the natural configuration to permit router-mediated swaps), every unprivileged user can bypass the allowlist entirely by routing through the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first parameter — the direct caller of the pool: [3](#0-2) 

When a user calls the pool directly, `sender` = user → the check is correct.  
When a user calls through `MetricOmmSimpleRouter`, `sender` = router address → the check evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin who wants to allow router-mediated swaps for their allowlisted users must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and the guard passes for **every** caller regardless of whether they are individually allowlisted. The per-user curation is completely defeated.

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through the supported periphery router. Any address can execute swaps against the pool's liquidity at oracle prices, draining LP value to unintended counterparties. This is a direct loss of the curation guarantee the pool admin configured and a broken core pool functionality (the allowlist guard fails open on the primary public swap path).

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the protocol. A pool admin who configures a swap allowlist and also wants to support router-mediated swaps for their allowlisted users will naturally add the router to the allowlist. This is the expected operational pattern, making the bypass reachable through normal, unprivileged use of the public router. No special permissions or malicious setup are required.

### Recommendation

The extension must gate by the economically relevant actor — the end user — not the intermediate router. Two approaches:

1. **Pass the end user through the router**: `MetricOmmSimpleRouter` should forward the original `msg.sender` (the end user) as an explicit parameter to the pool's `swap` call, and the pool should pass it as a separate `originator` field to extensions. This requires an interface change.

2. **Check recipient instead of sender for router flows**: Alternatively, the `SwapAllowlistExtension` could check both `sender` and `recipient`, but this is fragile.

The cleanest fix is option 1: add an `originator` field to the swap extension interface so the allowlist can always key on the true end user regardless of which supported periphery path reaches the pool.

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` as `extension1`, `beforeSwap` order set to call extension 1.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, router, true)` — the natural step to allow router-mediated swaps.
3. Disallowed user `alice` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(msg.sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → guard passes.
7. Alice's swap executes at oracle price against LP liquidity — the allowlist is bypassed.

The `FullMetricExtension.t.sol` integration test confirms the design: it allowlists `address(callers[0])` (the intermediate `TestCaller` contract), not `users[0]`, demonstrating that the current implementation gates the direct pool caller, not the end user. [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
