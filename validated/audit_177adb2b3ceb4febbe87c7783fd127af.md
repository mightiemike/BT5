Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any trader to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router's address rather than the actual trader. If the router is allowlisted (the only way to let allowlisted users use the router), every user on the network can bypass the allowlist by routing through the router. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
  recipient,
  ...
);
```

**Step 2 — `_beforeSwap` forwards `sender` unchanged to every configured extension.**

`ExtensionCalling._beforeSwap` encodes `sender` directly into the call to `IMetricOmmExtensions.beforeSwap`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `sender` (the pool's `msg.sender`) against the allowlist.**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()` — which is the **router**, not the end user, when the user goes through the periphery.

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender` to the pool.**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The original `msg.sender` (the end user) is stored only in transient callback context for payment purposes and is never forwarded to the pool's `swap` call as a parameter the extension can observe. The pool's `msg.sender` is always the router contract address.

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can bypass the allowlist |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers) can be fully bypassed by any unprivileged user simply by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The non-allowlisted user executes a real swap, receives output tokens, and the pool's access-control invariant is silently violated. This constitutes a direct break of the pool's core access-control functionality and enables unauthorized trading against LP funds in a pool explicitly designed to be restricted. The wrong value is `allowedSwapper[pool][router] == true` being accepted as authorization for an arbitrary end user.

## Likelihood Explanation

The router is the primary user-facing entrypoint for the protocol. Any user who discovers the allowlist restriction on a pool can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-transaction setup are required. The trigger is a single public call to `exactInputSingle`. The precondition — that the router is allowlisted — is the only operationally viable configuration for a pool that wants allowlisted users to use the router, making exploitation near-certain in any real deployment.

## Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic actor (the end user), not the intermediary contract. Two viable approaches:

1. **Preferred — trusted router forwarding**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` as a dedicated `realSender` parameter to `pool.swap`, and have the pool forward that value (rather than its own `msg.sender`) to extensions when the caller is a trusted/allowlisted router. This requires an interface change but preserves composability.

2. **Extension-level router awareness**: The extension could maintain a registry of trusted routers and, when `sender` is a known router, read the actual payer from a standardized slot (e.g., a callback context exposed by the router). This is more fragile but avoids a core interface change.

Checking `tx.origin` is not recommended as it breaks contract-to-contract composability and is a known anti-pattern.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only `alice` is allowlisted
pool.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it:
pool.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) calls the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// bob's swap succeeds — the extension saw sender=router (allowlisted), not bob
// allowedSwapper[pool][router] == true passes; bob receives output tokens
```

The extension checks `allowedSwapper[pool][router] == true` and passes. Bob receives output tokens from a pool he was never authorized to trade on.