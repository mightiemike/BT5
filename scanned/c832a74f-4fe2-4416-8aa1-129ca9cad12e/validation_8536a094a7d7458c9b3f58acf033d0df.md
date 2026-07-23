The call chain is fully traceable. Here is the verdict:

---

### Title
Swap Allowlist Bypassed via Allowlisted Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's allowlist status, not the end user's. Any unlisted EOA can bypass the allowlist by routing through an allowlisted router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

**Attack path:**
1. Pool admin creates a restricted pool and adds `MetricOmmSimpleRouter` to the allowlist (e.g., to permit router-mediated swaps for allowlisted users, or as a convenience integration).
2. Unlisted EOA calls `router.exactInputSingle(...)`.
3. Router calls `pool.swap(recipient, ...)` — router is `msg.sender`.
4. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. Unlisted EOA's swap executes despite never being individually permitted.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, and to any other contract (multicall, aggregator) that is itself allowlisted.

### Impact Explanation
The swap allowlist's core invariant — that only explicitly permitted addresses may swap — is broken for any pool where the router (or any other public intermediary) is on the allowlist. Unauthorized parties can execute swaps in pools intended to be restricted (e.g., KYC-gated, institutional, or compliance-restricted pools). This is broken core functionality under Sherlock contest rules.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the canonical periphery router. Pool admins who want to allow router-mediated swaps for their allowlisted users have no other option than to add the router to the allowlist, which simultaneously opens the pool to all router users. The bypass is trivially reachable by any EOA with no special privileges.

### Recommendation
The extension should check the economic actor (the end user), not the intermediary. Two options:

1. **Pass the originating user through `extensionData`**: the router encodes `msg.sender` (the EOA) into `extensionData`, and the extension decodes and checks it. This requires a trusted encoding convention.
2. **Mirror the `DepositAllowlistExtension` pattern**: introduce a `recipient` or `originator` field that the pool or router populates with the true end user, and gate on that instead of `sender`.

The simplest safe fix is to have the router encode the originating EOA in `extensionData` and have the extension decode and verify it when present, falling back to `sender` only for direct (non-router) calls.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_unlisted_eoa_bypasses_allowlist_via_router() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // EOA `attacker` is NOT on the allowlist
    address attacker = makeAddr("attacker");

    // Attacker calls through the router
    vm.startPrank(attacker);
    token0.approve(address(router), type(uint256).max);
    // This should revert NotAllowedToSwap but does NOT — router is allowlisted
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: address(token0),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    }));
    vm.stopPrank();
    // Swap succeeds — allowlist bypassed
}
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
