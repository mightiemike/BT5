### Title
`SwapAllowlistExtension` Checks Router Identity Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed from the pool. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted — not the actual user. If the pool admin allowlists the router (which is necessary for any allowlisted user to use the router), every unpermissioned address can bypass the swap allowlist by routing through the router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged as the `sender` argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly with no forwarding of the original caller: [4](#0-3) 

The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

The pool admin faces an impossible choice:

- **Router NOT allowlisted**: allowlisted users cannot use the router at all; they must call the pool directly.
- **Router IS allowlisted** (the only way to let legitimate users use the router): every address on the network can bypass the allowlist by routing through the router.

The unit tests for `SwapAllowlistExtension` call `extension.beforeSwap()` with `vm.prank(address(pool))` and pass the swapper address directly, so they never exercise the router path and do not catch this bypass: [5](#0-4) 

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners) is fully open to any address that routes through `MetricOmmSimpleRouter`. The attacker receives real token output from the pool; the pool's LP positions are exposed to unrestricted swap flow that the allowlist was designed to prevent. This breaks the swap-conservation and access-control invariants for any pool relying on this extension.

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point documented in the protocol. Any pool admin who wants allowlisted users to be able to use the standard router must allowlist the router address. This is the natural, expected configuration, making the bypass reachable by any unpermissioned address in a standard deployment.

### Recommendation

The `sender` argument forwarded to extension hooks must represent the **original human/EOA caller**, not the intermediate router. Two complementary fixes:

1. **In the router**: pass the original `msg.sender` through `callbackData` or a dedicated field so the pool can forward it to extensions as the true initiator.
2. **In `SwapAllowlistExtension`**: gate on the economically relevant actor. If the pool cannot supply the original caller, the extension should reject any `sender` that is itself a known router/contract unless that contract is explicitly allowlisted with a separate flag, and document that allowlisting the router opens the gate to all users.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can use the router.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(...)` — pool's `msg.sender` = router.
5. Pool calls `extension.beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. Attacker receives token output from the restricted pool, bypassing the allowlist entirely. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
