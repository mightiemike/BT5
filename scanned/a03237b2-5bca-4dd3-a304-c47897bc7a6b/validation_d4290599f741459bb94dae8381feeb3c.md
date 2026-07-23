### Title
SwapAllowlistExtension Allowlist Bypassed via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
The `SwapAllowlistExtension.beforeSwap` hook gates pool swaps by checking the `sender` argument passed from the pool, which is the direct `msg.sender` of the pool's `swap` call. When a user routes through the public `MetricOmmSimpleRouter`, the router becomes that `sender`. Any pool admin who allowlists the router address (the natural step to let allowlisted users reach the pool via the router) simultaneously opens the gate to every unprivileged user, because the extension cannot distinguish individual users behind the router.

### Finding Description

**Hook argument binding — pool side**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every before-swap extension:

```solidity
// MetricOmmPool.sol  (lines ~230-240)
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension.

**Guard logic — extension side**

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
// SwapAllowlistExtension.sol  (lines 31-41)
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whoever called `pool.swap()`.

**The bypass**

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)`. The pool therefore passes `address(router)` as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist. The moment `allowedSwapper[pool][router] = true`, every user — allowlisted or not — can bypass the gate by routing through the public router. The extension has no way to recover the original EOA from behind the router.

The same structural flaw applies to `DepositAllowlistExtension` for the `MetricOmmPoolLiquidityAdder` path (the `owner`/`sender` separation noted in the research targets), though that contract's full source was not retrieved in this session.

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (KYC-gated pool, institutional-only pool, whitelist-only launch) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can:

- Execute swaps in a pool that was intended to be access-controlled, draining liquidity reserved for allowlisted participants.
- Front-run or sandwich allowlisted traders whose orders are visible in the mempool.
- Interact with pools whose oracle pricing or fee structure was calibrated for a specific counterparty set.

This is a direct broken-invariant impact: the pool admin's configured access boundary is bypassed by an unprivileged path (the public router).

### Likelihood Explanation

Likelihood is **medium**. The trigger requires the pool admin to have allowlisted the router address. This is the natural operational step for any allowlisted pool