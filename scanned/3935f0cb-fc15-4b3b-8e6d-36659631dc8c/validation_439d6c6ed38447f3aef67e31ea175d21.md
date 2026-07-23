### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end user — any unprivileged actor bypasses the per-user allowlist by routing through `MetricOmmSimpleRouter` once the router is allowlisted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the allowlist entry consulted is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. A pool admin who allowlists the router — the natural step to let allowlisted users reach the pool through the periphery — simultaneously opens the gate to every unprivileged user who routes through the same contract.

### Finding Description

**Call chain for a router-mediated swap:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         └─ pool.swap(recipient, …)          // msg.sender = router
               └─ _beforeSwap(msg.sender=router, …)
                     └─ SwapAllowlistExtension.beforeSwap(sender=router, …)
                           check: allowedSwapper[pool][router]
```

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When the router is the caller, `sender` = router address. The check becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (so that allowlisted users can reach the pool through the periphery), this check passes for **every** caller of the router, regardless of whether they are individually allowlisted.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without forwarding the original `msg.sender`: [4](#0-3) 

The router's `multicall` uses `delegatecall`, which preserves `msg.sender` inside the router's own execution frame, but the subsequent `pool.swap(...)` call is still a regular external call from the router, so `msg.sender` seen by the pool is always the router. [5](#0-4) 

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties) and then allowlists the router so those addresses can use the periphery has, in effect, opened the pool to all users. Any unprivileged actor can call `router.exactInputSingle(...)` and the allowlist check passes because `allowedSwapper[pool][router] = true`. The attacker receives pool output tokens and the pool receives input tokens — a complete, fund-impacting bypass of the access-control guard. The pool admin has no on-chain signal that the guard is ineffective.

### Likelihood Explanation

The `SwapAllowlistExtension` NatSpec says it "Gates `swap` by swapper address, per pool." A pool admin naturally interprets "swapper" as the end user. To let those users reach the pool through the standard periphery they must allowlist the router — the exact action that collapses the guard. No privileged setup beyond the pool admin's own reasonable configuration is required; the attacker needs only to call the public router.

### Recommendation

Pass the economically relevant actor — the end user — through the hook rather than the immediate `msg.sender`. Two complementary approaches:

1. **Router-side**: Have `MetricOmmSimpleRouter` forward the original `msg.sender` as an `extensionData` field. The extension decodes and verifies it (with a pool-bound commitment so it cannot be spoofed from outside the router).

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` receives both `sender` (direct caller) and `extensionData`. Define a convention where the router encodes the real user in `extensionData`; the extension checks that field when `sender` is a known router, and falls back to `sender` for direct calls.

Either way, the allowlist must gate the identity that controls the economic action, not the intermediate dispatch contract.

### Proof of Concept

```solidity
// Setup: pool admin configures allowlist for user1 only, then allowlists router
// so user1 can use the periphery.
swapExtension.setAllowedToSwap(address(pool), user1, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // ← opens the gate

// Attacker (not allowlisted) bypasses the guard via the router:
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
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
// Swap succeeds: allowedSwapper[pool][router] == true
// attacker receives token1 output despite not being on the allowlist
```

The `beforeSwap` hook receives `sender = address(router)`, looks up `allowedSwapper[pool][router] = true`, and returns the success selector — the attacker's swap settles in full.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
