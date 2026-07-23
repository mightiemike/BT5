### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the per-user gate by routing through the same router.

### Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the pool sees `msg.sender = router`.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to the configured extension.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`.

The check never touches the actual user's address. The pool admin faces an impossible choice:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert, even for individually allowlisted users — broken core functionality |
| Router **allowlisted** | Every user on the network can swap through the router, bypassing the per-user gate entirely |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

**Relevant code:**

`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`: [1](#0-0) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool without forwarding the original caller: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router: [3](#0-2) 

`ExtensionCalling._beforeSwap` confirms `sender` is forwarded verbatim from the pool's `msg.sender`: [4](#0-3) 

### Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC-verified addresses, institutional partners). Once the pool admin allowlists the router to let those users trade via the standard periphery path, the allowlist is silently open to every address on the network. Any non-allowlisted user can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router and execute swaps against the pool's LP reserves. LP providers who deposited under the assumption that only trusted counterparties could trade against them suffer direct loss of principal through adverse selection or extraction by untrusted actors.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap interface documented for end-users. A pool admin who wants allowlisted users to be able to use the router will naturally allowlist the router address — this is the only way to make router swaps work at all. The admin has no on-chain signal that doing so opens the gate to everyone. The bypass requires no special privileges, no flash loans, and no multi-step setup: any EOA can call the router.

### Recommendation

The extension must recover the original user identity rather than relying on the `sender` argument, which reflects the immediate pool caller. Two sound approaches:

1. **Pass the original initiator through the pool.** Add an `initiator` field to the swap call or extension data so the pool can forward the true originating address to extensions. The extension then checks `allowedSwapper[pool][initiator]`.

2. **Require direct pool interaction for allowlisted pools.** Document and enforce that pools using `SwapAllowlistExtension` must not be accessed through the router, and add a factory-level flag or extension-level check that reverts when `sender` is a registered periphery contract.

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `trustedUser` is allowlisted.
// Admin allowlists the router so trustedUser can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not in allowlist) calls the router directly.
vm.prank(attacker); // attacker != trustedUser, not individually allowlisted
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000,
        amountOutMinimum: 0,
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Swap succeeds: extension checked allowedSwapper[pool][router] == true,
// never checked allowedSwapper[pool][attacker] == false.
```

The pool's `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the guard passes for every caller regardless of their individual allowlist status. [5](#0-4) [6](#0-5)

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
