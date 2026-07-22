### Title
`SwapAllowlistExtension` checks router address instead of original user, allowing any user to bypass per-user swap restrictions on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool` always sets to its own `msg.sender`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. A pool admin who allowlists the router (the only way to let their intended users use the standard periphery path) simultaneously opens the gate to every other user, completely defeating per-user curation.

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to every extension hook.**

In `MetricOmmPool.swap` the call to `_beforeSwap` is:

```solidity
_beforeSwap(
    msg.sender,   // ← always the immediate caller of the pool
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — `SwapAllowlistExtension.beforeSwap` keys the allowlist on that `sender`.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [2](#0-1) 

`msg.sender` here is the pool (correct pool-keying), but `sender` is the pool's `msg.sender` — the router when the user goes through the periphery.

**Step 3 — `MetricOmmSimpleRouter` calls the pool directly; the original user's address is never forwarded.**

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The original user (`msg.sender` of the router) is stored only in transient callback context for payment settlement; it is never passed to the pool's `swap` call. The pool therefore sees `msg.sender = router`.

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.**

The admin's only options are:
- **Do not allowlist the router** → intended allowlisted users cannot use the standard periphery path at all.
- **Allowlist the router** → every user on the network can bypass the per-user restriction by calling `exactInputSingle` / `exactInput` / `exactOutput` through the router.

There is no configuration that simultaneously allows intended users to use the router and blocks unintended users.

### Impact Explanation

Any unprivileged user can trade on a pool whose admin intended to restrict access to a curated set of counterparties (KYC, whitelist-only, institutional-only pools). The attacker calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool. If the router is allowlisted (the only way for legitimate users to use the periphery), the `beforeSwap` hook passes unconditionally for the attacker. The attacker executes swaps at oracle-derived prices, draining liquidity that was deposited under the assumption that only approved counterparties would trade. LP principals are at risk because the pool's risk model (spread, bin sizing, fee calibration) was designed for a known, trusted set of traders.

**Severity: High** — direct loss of LP principal is reachable by any unprivileged caller through the standard public router path, with no special preconditions beyond the admin having allowlisted the router.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical, documented periphery entry point. Any pool admin who wants their allowlisted users to use the standard UX must allowlist the router. The moment they do, the bypass is open to everyone. The attacker needs no special privileges, no tokens pre-positioned, and no admin cooperation — only the ability to call a public function.

### Recommendation

1. **Forward the original user through the router.** Add an explicit `address sender` parameter to `IMetricOmmPoolActions.swap` (or encode it in `extensionData` under a trusted-router convention) so the pool can pass the true originator to extension hooks instead of its own `msg.sender`.

2. **Alternatively, have the extension decode the original user from `extensionData`.** The router would ABI-encode `msg.sender` into `extensionData`; the extension would decode and check it. This requires the extension to trust only calls whose `extensionData` is well-formed, which can be enforced by checking that `msg.sender` (the pool) is a known factory pool.

3. **Document the limitation clearly** in `SwapAllowlistExtension` NatDoc: allowlisting the router grants access to all router users, not just the intended subset.

### Proof of Concept

```
Setup:
  pool = curated MetricOmmPool with SwapAllowlistExtension configured
  admin allowlists alice (EOA) so she can trade
  admin also allowlists router so alice can use the standard UI

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: curated_pool,
        tokenIn: token0,
        amountIn: large_amount,
        ...
    })

  Flow:
    router.exactInputSingle → pool.swap(msg.sender=router, ...)
    pool._beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap:
      allowedSwapper[pool][router] == true  ← admin set this for alice
      → hook passes
    charlie's swap executes at oracle price
    charlie receives token1 output; pool LP balances decrease

Result:
  charlie bypassed the per-user allowlist entirely.
  alice's allowlist entry is irrelevant; the router entry is the effective gate.
  Any user can repeat this attack.
``` [4](#0-3) [5](#0-4) [3](#0-2)

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
