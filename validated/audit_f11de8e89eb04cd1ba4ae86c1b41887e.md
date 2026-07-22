The vulnerability is real. Here is the complete trace:

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension**

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

**Step 2 — Router calls `pool.swap()` directly, so `msg.sender` in the pool is the router, not the end user**

In `MetricOmmSimpleRouter.exactInputSingle`, the pool's `swap()` is called with no forwarding of the original caller: [2](#0-1) 

**Step 3 — Extension checks `sender` (the router), not the end user**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The `sender` parameter is the router address, not the original end user who called the router. If the pool admin allowlists the router (`allowedSwapper[pool][router] = true`), every user who routes through it passes the check regardless of their own allowlist status.

---

### Title
SwapAllowlistExtension checks router address instead of end-user identity, allowing any user to bypass per-pool swap allowlist via an allowlisted router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the router contract, not the original caller. The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. If the pool admin has added the router to the allowlist (a natural configuration to permit router-based swaps), the check passes for **every** caller of the router, including addresses explicitly excluded from the allowlist.

### Impact Explanation
The swap allowlist is the primary pool curation mechanism. Any non-allowlisted address can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` targeting an allowlisted pool. This completely nullifies the allowlist's access-control guarantee: restricted pools become effectively open to all users who know the router address. This is broken core pool functionality under the contest rules.

### Likelihood Explanation
The scenario requires only that the pool admin has allowlisted the router (a standard, expected configuration for any pool that wants to support router-based trading). No privileged access, malicious setup, or non-standard token behavior is needed. Any attacker can exploit this permissionlessly.

### Recommendation
The extension must check the **original end user**, not the intermediary. Two options:

1. **Pass the original initiator through the router**: Store `msg.sender` (the end user) in transient storage at router entry and expose it via a standard interface (e.g., `IMetricOmmRouter.swapInitiator()`). The extension reads this value when `msg.sender` is a known router.
2. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist routers, and instead allowlist individual end users only. This is operationally fragile and not enforceable on-chain.

Option 1 is the robust fix.

### Proof of Concept
```solidity
// Pool admin setup:
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT added to the allowlist

// Attacker exploits:
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Swap succeeds; attacker receives token1 despite not being on the allowlist.
// allowedSwapper[pool][attacker] == false, but check passed because allowedSwapper[pool][router] == true
```

The pool's `_beforeSwap` receives `sender = address(router)`, the extension checks `allowedSwapper[pool][router] = true`, and the swap proceeds. [4](#0-3) [1](#0-0) [5](#0-4)

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
