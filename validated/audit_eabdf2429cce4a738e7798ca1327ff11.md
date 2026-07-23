Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` validates the `sender` argument forwarded by the pool, but `MetricOmmPool.swap` sets that argument to its own `msg.sender` — which is the router contract when a user swaps through `MetricOmmSimpleRouter`. As a result, the extension checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. Any address can bypass the allowlist by routing through the public router contract once the pool admin allowlists it.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that argument against its per-pool allowlist, using `msg.sender` (the pool) as the mapping key: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly from its own address: [3](#0-2) 

At that point `msg.sender` inside the pool is the **router contract**, so `sender` forwarded to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The actual user's identity is never visible to the guard. The same substitution occurs for `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` directly from the router: [4](#0-3) 

The pool admin faces an impossible choice: not allowlisting the router blocks all legitimate router users, while allowlisting the router opens the pool to every address on the network. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a defined set of counterparties (e.g., KYC-verified addresses, institutional partners). Once the pool admin allowlists the router — a necessary step for any legitimate user who needs multi-hop routing, slippage protection, or deadline enforcement — the restriction is nullified for the entire public. LP providers who deposited under the assumption that only vetted counterparties could trade against them are exposed to unrestricted flow, including adversarial or regulatory-non-compliant actors. This constitutes a broken core access-control invariant with direct exposure of LP principal to unintended counterparties, qualifying as a High severity broken core pool functionality finding.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router, which is a routine operational step for any pool that expects users to interact through the standard periphery. The attacker needs no special privilege, no token approval beyond what a normal swap requires, and no knowledge of the pool's internal state. The attack is a single public function call on a deployed contract, executable by any address at any time after the router is allowlisted.

## Recommendation
The `sender` identity forwarded through the hook chain must reflect the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` (the real user) through `extensionData` in a standardised envelope, and have `SwapAllowlistExtension` decode and verify it — combined with a check that `msg.sender` (the pool's caller) is a known trusted router, so the field cannot be spoofed by a direct caller.

2. **Extension-side**: Add a `trustedForwarder` registry to `SwapAllowlistExtension`. When `sender` is a registered forwarder, decode the real swapper from `extensionData`; otherwise use `sender` directly. This mirrors the ERC-2771 meta-transaction pattern and keeps the pool core unchanged.

Either approach must ensure that a direct pool call (no router) still uses `sender` as-is, so the guard cannot be bypassed by omitting `extensionData`.

## Proof of Concept
```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the legitimate user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({
           pool:      restrictedPool,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(...) — msg.sender inside pool = router address.
6. Pool calls extension.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. Bob's swap executes against the restricted pool.

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; Bob trades against LP providers who expected only allowlisted counterparties.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
