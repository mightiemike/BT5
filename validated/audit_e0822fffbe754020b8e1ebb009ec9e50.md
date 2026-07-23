The call chain is fully traceable. Here is the exact flow:

1. End user calls `MetricOmmSimpleRouter.exactInputSingle(...)` — `msg.sender` to the router = end user
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — `msg.sender` to the pool = **router address**
3. Pool's `swap` passes `msg.sender` as `sender` to `_beforeSwap`
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The extension checks whether the **router** is allowlisted, never the end user.

The full call chain is confirmed. Here is the definitive analysis:

**Call chain:**
- `MetricOmmSimpleRouter.exactInputSingle` is called by end user (`msg.sender` = end user)
- Router calls `pool.swap(...)` — so `msg.sender` to the pool = **router address**
- Pool's `swap` passes `msg.sender` (router) as `sender` to `_beforeSwap`
- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The extension checks whether the **router** is allowlisted, never the actual end user.

---

### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user routing through an allowlisted router to bypass the swap guard — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of the pool, which is the router contract when swaps are routed through `MetricOmmSimpleRouter`. The check `allowedSwapper[pool][sender]` therefore tests whether the **router** is allowlisted, not the actual end user. Any pool admin who allowlists the router address (the natural action to permit router-based swaps) inadvertently opens the pool to every user of that router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When the swap originates from `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap`: [4](#0-3) 

So `sender` = router address. The extension never sees the original end user (`msg.sender` to the router). The pool admin has no way to allowlist individual end users while also permitting router-based swaps — allowlisting the router grants access to every user of that router.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses is rendered effectively permissionless for any user who routes through `MetricOmmSimpleRouter`. Unauthorized traders can execute swaps against the pool's LP positions, directly violating the permissioned-pool invariant and exposing LP capital to trades the pool admin intended to block.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the standard periphery swap interface. Any pool admin who enables the swap allowlist and also wants their authorized users to use the router will naturally allowlist the router address, triggering the bypass for all other users. The existing test `test_allowedSwapSucceeds` in `FullMetricExtensionTest` already demonstrates this pattern — it allowlists `callers[0]` (the intermediary contract), not `users[0]` (the end user): [5](#0-4) 

### Recommendation
Pass the original initiator through the call chain. One approach: `MetricOmmSimpleRouter` stores `msg.sender` in transient storage before calling `pool.swap` and encodes it in `extensionData`; the extension reads it from there. Alternatively, the pool could accept an explicit `originator` parameter that the router fills with `msg.sender`. Either way, `beforeSwap` must check the actual end user, not the pool's immediate caller.

### Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists the router: swapExtension.setAllowedToSwap(pool, address(router), true)
// 3. Unauthorized user calls router:
vm.prank(unauthorizedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    ...
}));
// 4. Swap succeeds — allowedSwapper[pool][router] == true, end user never checked
// 5. Assert unauthorizedUser received output tokens despite not being on the allowlist
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
