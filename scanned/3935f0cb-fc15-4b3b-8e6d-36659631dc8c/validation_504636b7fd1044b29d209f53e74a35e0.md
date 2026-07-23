### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Allowlist Bypass or Blocking Allowlisted Users - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`, making the allowlist guard check the wrong actor on every router-mediated swap.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The router never forwards the original caller's address to the pool. There is no mechanism in the pool's `swap()` signature to carry a "true originator" separate from `msg.sender`. The result is that the extension evaluates `allowedSwapper[pool][routerAddress]` for every router-mediated swap, regardless of who the actual user is.

### Impact Explanation

Two fund-impacting outcomes follow directly:

**Bypass (High):** If the pool admin allowlists the router address — a natural configuration when the router is considered a trusted periphery contract — every user, including those the allowlist was designed to exclude, can swap freely by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection against any user who knows to use the router.

**Denial (High):** If the pool admin allowlists specific user addresses but not the router, those allowlisted users cannot execute swaps through the supported periphery path. Their transactions revert with `NotAllowedToSwap` even though they are explicitly permitted. This breaks the core swap flow for the intended user set.

Both outcomes violate the stated invariant: *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* [5](#0-4) 

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented and deployed alongside the protocol. Any pool that configures `SwapAllowlistExtension` and expects users to interact through the router will immediately exhibit one of the two failure modes above. No special conditions, flash loans, or oracle manipulation are required — a single `exactInputSingle` call is sufficient to trigger the wrong-actor check.

### Recommendation

The pool must pass the original economic actor's address to the extension, not the intermediary's. Two approaches:

1. **Preferred — router passes originator via `extensionData`:** Define a convention where the router encodes `msg.sender` (the actual user) into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct pool calls.

2. **Alternative — pool exposes an originator field:** Add an explicit `originator` parameter to `pool.swap()` that the router populates with `msg.sender`, and pass it through `_beforeSwap` to extensions. Extensions then check `originator` instead of `sender`.

### Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension.
// Admin allowlists the router (treating it as a trusted periphery contract).
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker — not individually allowlisted — calls the router.
// Extension checks allowedSwapper[pool][router] == true → passes.
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds — allowlist bypassed.

// Conversely: admin allowlists alice but not the router.
swapExtension.setAllowedToSwap(address(pool), alice, true);

vm.prank(alice);
router.exactInputSingle(...); // reverts NotAllowedToSwap — alice blocked despite being allowlisted.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
