The call chain is fully traceable and confirms the vulnerability. Here is the complete analysis:

**Exact call chain:**

1. Alice calls `router.exactInputSingle(...)` — `msg.sender` to router = alice
2. Router calls `pool.swap(params.recipient, ...)` — `msg.sender` to pool = **router address** [1](#0-0) 
3. Pool calls `_beforeSwap(msg.sender, ...)` — `sender` = **router address** [2](#0-1) 
4. Extension receives `beforeSwap(sender=router, ...)` and checks `allowedSwapper[pool][router]` [3](#0-2) 

The check `allowedSwapper[msg.sender][sender]` uses `msg.sender = pool` and `sender = router`. Alice's individual allowlist entry (`allowedSwapper[pool][alice]`) is never consulted.

---

### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, permanently blocking allowlisted users from swapping through the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension` is designed to gate swaps per individual user address. However, when a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router` and passes that as `sender` to `beforeSwap`. The extension then checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][alice]`. An individually allowlisted user is permanently blocked from using the router.

### Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- router address, not alice
  recipient,
  ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this `sender` directly to the extension: [4](#0-3) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

Here `msg.sender = pool` and `sender = router`. Alice's entry `allowedSwapper[pool][alice]` is never read. The only way to pass the check is if `allowedSwapper[pool][router] = true`, which grants all users access through the router — defeating the purpose of individual allowlisting entirely.

### Impact Explanation
Any pool deploying `SwapAllowlistExtension` with per-user allowlisting suffers complete loss of swap functionality for all allowlisted users who use the router (the standard protocol entry point). The only workaround — allowlisting the router itself — removes all individual access control. This is broken core pool functionality causing loss of usable swap flows, meeting the contest's "broken core pool functionality" impact gate.

### Likelihood Explanation
High. The router is the standard and expected way to interact with Metric OMM pools. Any pool admin who deploys `SwapAllowlistExtension` and allowlists individual users (the extension's documented purpose) will immediately encounter this failure for every router-mediated swap. No attacker action is required; the misconfiguration is structural.

### Recommendation
The extension should identify the true originating user. Two options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (alice) into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check both `sender` and a recovered origin**: Add a secondary allowlist for intermediaries (routers), and when `sender` is an allowlisted intermediary, require the true origin to be passed and verified.

The cleanest fix is for the pool or router to propagate the true originating user as a distinct parameter, separate from the immediate `msg.sender`.

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_allowlistedUserBlockedThroughRouter() public {
    // alice is individually allowlisted
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    // router is NOT allowlisted
    // allowedSwapper[pool][router] == false

    vm.prank(alice);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            recipient: alice,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // Reverts NotAllowedToSwap even though alice is allowlisted.
    // The extension checked allowedSwapper[pool][router], not allowedSwapper[pool][alice].
}
```

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
