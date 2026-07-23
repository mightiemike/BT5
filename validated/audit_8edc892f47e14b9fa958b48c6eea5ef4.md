Audit Report

## Title
SwapAllowlistExtension checks the router address instead of the end user, allowing any user to bypass per-user swap restrictions via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to support standard periphery usage inadvertently grants every user on the network unrestricted swap access to that pool.

## Finding Description
The call chain is fully confirmed by production code:

**Step 1:** `MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Step 2:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with `params.recipient` (the end user) as `recipient`, while the router itself becomes `msg.sender` of the pool call: [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` receives `sender = router` and checks `allowedSwapper[pool][router]`: [3](#0-2) 

If `allowedSwapper[pool][router]` is `true`, the check passes unconditionally for any caller of the router. The actual end user's address (`recipient`) is the second argument to `beforeSwap` but is entirely ignored (named `address,` with no variable binding).

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — the address that receives LP shares — which is the economically relevant actor and cannot be spoofed through a router intermediary.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC-verified addresses, whitelisted counterparties, or protocol-internal addresses provides zero access control once the router is allowlisted. Any unprivileged user can call `exactInputSingle` or `exactInput` on the public router, receive output tokens, and pay input tokens — fully executing a swap in a pool that was supposed to bar them. The `allowedSwapper[pool][attacker]` mapping is never consulted. This constitutes broken core pool functionality: the allowlist extension's stated purpose ("Gates `swap` by swapper address, per pool") is entirely defeated through the standard periphery path.

## Likelihood Explanation
The precondition — the router being allowlisted — is the natural and expected configuration for any pool that wants to support the standard periphery UX. A pool admin who independently (and reasonably) configures both a `SwapAllowlistExtension` and allowlists the router will unknowingly negate the restriction for all users. No special privileges, flash loans, or unusual token behavior are required. Any unprivileged user can exploit this by calling the public router. The interaction between the two configurations is undocumented and unguarded.

## Recommendation
The extension must gate the economically relevant actor. The cleanest fix consistent with `DepositAllowlistExtension`'s design is to check `recipient` (the address that receives output tokens) instead of `sender`:

```solidity
// SwapAllowlistExtension.sol L31 — change signature to bind recipient
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

This mirrors how `DepositAllowlistExtension` checks `owner` (the LP share recipient) rather than `msg.sender` (the payer/router). [3](#0-2) 

## Proof of Concept
**Setup:**
- Pool deployed with `SwapAllowlistExtension` as a `beforeSwap` hook
- Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps
- Pool admin does NOT call `setAllowedToSwap(pool, attacker, true)`

**Attack:**
```solidity
// attacker calls the public router — no special permissions needed
router.exactInputSingle(
    ExactInputSingleParams({
        pool: restrictedPool,
        tokenIn: token0,
        recipient: attacker,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// pool.swap is called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
// allowedSwapper[pool][attacker] is never checked
// attacker receives token1 output from a pool they were supposed to be barred from
```

**Result:** The attacker successfully swaps in a restricted pool. The allowlist is fully bypassed.

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
