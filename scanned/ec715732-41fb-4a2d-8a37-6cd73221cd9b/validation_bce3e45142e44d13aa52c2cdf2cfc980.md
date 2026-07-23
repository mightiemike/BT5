### Title
SwapAllowlistExtension gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router — a necessary step for any allowlisted user to use the router — every user, including non-allowlisted ones, can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong identity checked by the swap allowlist**

`MetricOmmPool.swap` passes `msg.sender` (the direct caller) as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist, keyed by the pool (`msg.sender` inside the extension):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 71-80
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

`msg.sender` to the pool is the **router**, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The impossible configuration dilemma**

The pool admin faces a forced choice with no correct option:

| Configuration | Effect |
|---|---|
| Allowlist specific users only | Those users can only swap directly; the router is blocked for everyone |
| Allowlist the router | Every user on-chain can bypass the per-user allowlist via the router |

There is no configuration that allows specific users to use the router while blocking others.

**Contrast with the deposit allowlist — which is correct**

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` argument (the economic actor), not the `sender` (the technical caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The deposit allowlist correctly gates the position owner regardless of who calls `addLiquidity`. The swap allowlist should analogously gate the actual user, but instead gates the technical caller, which is the router when the router is used.

The same bypass applies to `exactInput` (multi-hop) and `exactOutput` / `exactOutputSingle`, all of which call `pool.swap` from the router contract.

---

### Impact Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., KYC-gated, institutional-only, or MEV-protected pools) is rendered unenforceable the moment the pool admin allowlists the router. Because allowlisting the router is the only way to let allowlisted users access the router, the pool admin is forced into a configuration that opens the pool to all users. Non-allowlisted users can then swap freely, extracting value from LPs who expected a restricted counterparty set. This is a direct loss of LP principal through trades the pool was explicitly configured to block.

---

### Likelihood Explanation

The trigger is a pool admin allowlisting the router — a natural and expected action when the pool admin wants allowlisted users to be able to use the standard periphery. The admin is not acting maliciously; the misconfiguration is invisible because the allowlist UI (`setAllowedToSwap(pool, router, true)`) gives no indication that it opens the pool to all users. Any pool that combines `SwapAllowlistExtension` with the public router is vulnerable once the router is added to the allowlist.

---

### Recommendation

Gate the actual user, not the technical caller. Two concrete options:

1. **Mirror the deposit allowlist pattern**: have the pool pass a user-supplied `recipient` or a decoded address from `extensionData` as the identity to check, rather than `msg.sender`.

2. **Router-forwarded identity**: have `MetricOmmSimpleRouter` encode `msg.sender` (the real user) into `extensionData`, and have `SwapAllowlistExtension` decode and check that address when the caller is a known router. This requires the extension to trust the router, which must itself be verified.

The simplest safe fix is to check `recipient` instead of `sender` in `beforeSwap`, since `recipient` is the address that receives the output tokens and is the economically relevant actor — analogous to `owner` in the deposit path.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Alice wants to use the router, so the pool admin also allowlists the router: `setAllowedToSwap(pool, router, true)`.
4. Non-allowlisted Bob calls `router.exactInputSingle({