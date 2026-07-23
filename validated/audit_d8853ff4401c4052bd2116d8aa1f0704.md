Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's allowlist status rather than the actual user's. Any pool admin who allowlists the router to enable standard UI access simultaneously opens the allowlist bypass to every address on the network.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is on the per-pool allowlist, where `msg.sender` inside the extension is the pool:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The pool therefore passes `sender = address(router)` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The actual caller's identity (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the extension hook. [4](#0-3) 

**Corrupted invariant:** `allowedSwapper[pool][bob] == false`, yet Bob's swap settles successfully because the extension only sees `allowedSwapper[pool][router] == true`.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool's liquidity, draining LP value or violating regulatory/operational constraints the pool admin intended to enforce. This is a direct, complete bypass of the configured access-control guard with no additional privilege required beyond calling a public periphery contract. The impact is loss of the intended access-control invariant, enabling unauthorized fund flows through the pool.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entry point for end users. Any pool admin who configures `SwapAllowlistExtension` and also allowlists the router — the natural operational choice to make the pool usable through the standard UI — immediately opens the bypass to all users. The attacker needs no special role, no flash loan, and no oracle manipulation; only a standard router call is required. The precondition (router allowlisted) is the expected operational state for any pool that intends to support UI-based trading.

## Recommendation
The extension must gate on the economic actor (the human or contract that controls the funds and benefits from the trade), not on the intermediary router. Two complementary fixes:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This is acceptable given the router is a protocol-controlled contract.

2. **Add an `originator` field to the `beforeSwap` hook interface:** Extend `IMetricOmmExtensions.beforeSwap` with an explicit `originator` address that the pool populates from a transient-storage context set by the router before calling `pool.swap`. The extension then checks `originator` instead of `sender`.

Until fixed, pool admins must **not** allowlist the router address on pools that use `SwapAllowlistExtension`; instead they should require users to call `pool.swap` directly.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as `EXTENSION_1` with `BEFORE_SWAP_ORDER` pointing to it.
2. Admin calls `extension.setAllowedToSwap(pool, alice, true)` — Alice is KYC'd.
3. Admin calls `extension.setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the UI.
4. Bob (not KYC'd, not individually allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: pool,
       recipient: bob,
       zeroForOne: true,
       amountIn: 1_000e18,
       amountOutMinimum: 0,
       priceLimitX64: 0,
       tokenIn: token0,
       extensionData: "",
       deadline: block.timestamp
   }));
   ```
5. Router calls `pool.swap(bob, true, ..., "")` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives token1 output; the allowlist guard was never applied to Bob's identity.

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
