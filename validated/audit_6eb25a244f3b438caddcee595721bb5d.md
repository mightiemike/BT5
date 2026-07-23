Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass When Router Is Whitelisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which `MetricOmmPool.swap` sets to its own `msg.sender`. When `MetricOmmSimpleRouter` intermediates a swap, `sender` is the router address, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their permitted users inadvertently opens the pool to every caller on the network, completely defeating the allowlist invariant.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool (the extension caller) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, it calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,   // output destination only
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The router never passes the original user's address as `sender`; it only passes `params.recipient` as the output destination. The pool therefore sees `msg.sender = router` and calls `_beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]`.

A pool admin who wants their allowlisted users to trade through the router has no choice but to call `setAllowedToSwap(pool, address(router), true)`. Once this is done, `allowedSwapper[pool][router]` is `true` for every call, so any address that calls any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) passes the guard regardless of whether that address is individually permitted.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` is intended to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners). Once the router is allowlisted, any unprivileged address can execute swaps against the pool's liquidity at oracle prices. This constitutes a direct loss of LP principal through adverse selection and a complete failure of the pool's curation invariant — a broken core pool functionality causing loss of funds, meeting the contest's Critical/High impact threshold.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery entry point providing multi-hop routing, slippage protection, and deadline enforcement. Pool admins deploying curated pools will naturally want their allowed users to benefit from these features. Adding the router to the allowlist is the only mechanism available to them, making this a predictable and near-certain operational step. No special attacker capability is required beyond calling a public router function once the router is listed.

## Recommendation
The extension must gate on the end user, not the intermediate caller. Two complementary approaches:

1. **Pass the originator through `extensionData`.** The router should encode `msg.sender` into `extensionData` (e.g., as a leading `address`). The extension, when `sender` is a known/registered router, decodes the originator from `extensionData` and applies the allowlist check against that address instead.

2. **Register trusted routers.** Add a `trustedRouter` mapping to `SwapAllowlistExtension`. In `beforeSwap`, if `allowedSwapper[pool][sender]` is false but `trustedRouter[sender]` is true, decode and check the originator from `extensionData`.

Alternatively, document explicitly that adding any router to the allowlist opens the pool to all users, and provide a separate `RouterAllowlistExtension` that enforces originator-based checks.

## Proof of Concept
**Setup:**
- Pool deployed with `SwapAllowlistExtension` as `beforeSwap` extension.
- Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
- Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — to let Alice use the router.

**Attack (Bob, not on allowlist):**
```solidity
// Bob calls the router directly
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:             address(pool),
    recipient:        bob,
    zeroForOne:       true,
    amountIn:         1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:    0,
    deadline:         block.timestamp,
    tokenIn:          token0,
    extensionData:    ""
}));
```

**Trace:**
1. `router.exactInputSingle` → `pool.swap(bob, true, ...)` with `msg.sender = router`.
2. Pool calls `_beforeSwap(router, bob, ...)`.
3. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
4. Swap executes; Bob receives token1 output.

Bob successfully trades in a pool he was never permitted to access. The allowlist is fully bypassed.