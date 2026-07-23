The code path is fully traceable. Here is the analysis:

**Call chain:**

1. EOA calls `MetricOmmSimpleRouter.exactInputSingle(...)` — `msg.sender` = EOA
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, ...)` — `msg.sender` inside pool = **router address**
3. Pool calls `_beforeSwap(msg.sender, ...)` — passes **router address** as `sender`
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The original EOA identity is **never forwarded**. The extension always sees the router as the swapper.

---

### Title
SwapAllowlistExtension per-user swap gate is bypassed for all router-mediated swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of `pool.swap`. When `MetricOmmSimpleRouter` calls `pool.swap`, the pool's `msg.sender` is the router contract, not the originating EOA. The allowlist therefore gates the router address, not the individual user.

### Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original caller's address: [4](#0-3) 

This creates an irreconcilable dilemma for pool admins:

- **If they allowlist individual EOAs:** those EOAs cannot swap through the router (the hook sees `router`, not the EOA → reverts). Core router functionality is broken for allowlisted pools.
- **If they allowlist the router address:** every EOA on the network can bypass the per-user gate by routing through the public router, since the hook only checks `allowedSwapper[pool][router]`.

### Impact Explanation
The swap allowlist is the primary access-control mechanism for restricting which addresses may trade against a pool. The bypass allows any unlisted EOA to execute swaps against a pool that the admin intended to restrict. This breaks the core pool functionality (access-controlled trading) and can cause direct fund loss if the pool is designed to serve only KYC'd or whitelisted counterparties (e.g., institutional pools, compliance-gated pools). The router is a public, permissionless contract, so no special privilege is required.

### Likelihood Explanation
The router is the standard, documented swap entrypoint for end users. Any user who discovers the mismatch can trivially route through `MetricOmmSimpleRouter` to bypass the allowlist. No admin cooperation, flash loan, or special token behavior is required.

### Recommendation
The pool should pass a dedicated `sender` parameter (the original caller's address) through `pool.swap` rather than using `msg.sender`. Alternatively, the router should encode the original `msg.sender` into `extensionData` and the extension should decode and verify it — but this requires a trusted encoding scheme. The cleanest fix is to add an explicit `sender` parameter to `IMetricOmmPoolActions.swap` so the router can forward `msg.sender` to the pool, which then passes it to all extension hooks.

### Proof of Concept
```
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists only Alice
ext.setAllowedToSwap(pool, alice, true);

// Alice swaps directly → passes (sender = alice)
vm.prank(alice);
pool.swap(alice, true, 1000, 0, "", "");

// Bob (not allowlisted) swaps directly → reverts (sender = bob)
vm.prank(bob);
vm.expectRevert(NotAllowedToSwap.selector);
pool.swap(bob, true, 1000, 0, "", "");

// Bob routes through the public router → sender seen by hook = router address
// If router is allowlisted (necessary for Alice to use router too):
ext.setAllowedToSwap(pool, address(router), true);
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// → passes! allowedSwapper[pool][router] = true, bob bypasses the per-user gate
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
