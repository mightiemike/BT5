The call path is fully traceable. Let me confirm the exact `sender` binding at each hop.

**Call chain:**

1. EOA → `MetricOmmSimpleRouter.exactInputSingle` (`msg.sender` = EOA)
2. Router → `pool.swap(params.recipient, ...)` — `msg.sender` to pool = **router address**
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — `sender` = **router address** [1](#0-0) 
4. `ExtensionCalling._beforeSwap` encodes `sender` (router) and calls `SwapAllowlistExtension.beforeSwap` [2](#0-1) 
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]` [3](#0-2) 

The `sender` checked is the **router address**, not the originating EOA. This is confirmed and unambiguous.

---

### Title
SwapAllowlistExtension checks router address instead of end-user, enabling allowlist bypass for any EOA via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps using the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` — the immediate caller of the pool. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the originating EOA. If the pool admin allowlists the router address (a natural configuration for router-mediated pools), every EOA — including explicitly non-allowlisted ones — can bypass the restriction by calling the router.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- router address when called via router
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [4](#0-3) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the router is allowlisted, the gate passes for **any** EOA that calls through the router, regardless of whether that EOA is individually allowlisted.

This creates two broken states:
- **Bypass**: Admin allowlists the router → all EOAs can swap, defeating the allowlist entirely.
- **False block**: Admin allowlists specific EOAs → those EOAs cannot swap via the router (router not allowlisted), breaking normal usage.

There is no configuration that simultaneously allows router-mediated swaps and correctly gates individual end-users.

`MetricOmmSimpleRouter.exactInputSingle` sets the payer as `msg.sender` (the EOA) in transient callback context, but the pool's `swap` call still originates from the router:

```solidity
// MetricOmmSimpleRouter.sol:71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, ...);
``` [5](#0-4) 

The EOA identity is only stored in transient callback context for payment purposes; it is never forwarded to the pool's `swap` call as the `sender`.

### Impact Explanation

Any EOA can trade on a restricted pool by routing through `MetricOmmSimpleRouter` if the router is allowlisted. The allowlist — the sole access-control mechanism for curated pools — is rendered ineffective. Disallowed users gain full swap access to pools intended to be restricted (e.g., institutional-only, KYC-gated, or whitelist-only pools), which constitutes broken core pool functionality and a direct admin-boundary break.

### Likelihood Explanation

The router is the standard, documented swap entry point for end-users. A pool admin configuring a curated pool with `SwapAllowlistExtension` would naturally allowlist the router to permit router-mediated swaps for their approved users. This is the expected operational pattern, making the misconfiguration highly likely in practice. No privileged access or special conditions are required for the attacker — any EOA can call `exactInputSingle` on the router.

### Recommendation

The `sender` forwarded to extension hooks should reflect the **originating user**, not the immediate pool caller. Two approaches:

1. **Pass EOA through the router**: Have `MetricOmmSimpleRouter` forward `msg.sender` as an explicit `sender` field in `callbackData` or `extensionData`, and have `SwapAllowlistExtension` decode and verify it (with the pool verifying the router is a trusted forwarder).
2. **Check `tx.origin` as a fallback**: Not recommended in general, but acceptable for allowlist-only checks where the goal is to gate the originating EOA.

The cleaner fix is option 1: the router encodes the originating EOA in `extensionData`, and `SwapAllowlistExtension` reads it when `msg.sender` (the pool's caller) is a known trusted router, falling back to `sender` for direct calls.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistBypass_viaRouter() public {
    // Setup: deploy pool with SwapAllowlistExtension
    // Admin allowlists ONLY the router (not the attacker EOA)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    address attacker = makeAddr("attacker");
    // attacker is NOT allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Fund attacker and approve router
    token0.mint(attacker, 1_000e18);
    vm.prank(attacker);
    token0.approve(address(router), type(uint256).max);

    // Attacker routes through the router — swap succeeds despite not being allowlisted
    vm.prank(attacker);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
```

The pool's `_beforeSwap` receives `sender = address(router)`, which is allowlisted, so `SwapAllowlistExtension` passes. The attacker EOA is never checked.

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
