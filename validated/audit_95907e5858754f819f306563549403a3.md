Audit Report

## Title
`SwapAllowlistExtension` gates on router address instead of real swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` at the pool's `swap` call site. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the real user. Because the router must itself be allowlisted for any legitimate user to use it, allowlisting the router grants every caller of the router implicit swap permission on the restricted pool, regardless of whether they were individually allowlisted.

## Finding Description

**Root cause — pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  lines 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every registered extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  lines 149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
```

**Extension check gates on that `sender`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  lines 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()` — which is the router when the user enters through `MetricOmmSimpleRouter`.

**Router never forwards real user identity:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  lines 71-80
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

The real user's address (`msg.sender`) is stored only in transient storage for the payment callback. It is never passed to `pool.swap()`. The pool therefore sees `msg.sender = router` and passes that to `_beforeSwap` as `sender`.

**The dilemma is inescapable:** For any allowlisted user to use the router, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true` for every caller, so the extension passes for every user who routes through `MetricOmmSimpleRouter` — including users who were never individually allowlisted.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly gates on `owner` (the second argument, which is the position owner regardless of intermediary), not `sender`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  lines 32-42
function beforeAddLiquidity(address, address owner, ...)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The swap extension has no equivalent identity-preserving argument — it gates on the caller identity, which collapses to the router for all router-mediated swaps.

**Existing tests confirm the gap:** `FullMetricExtension.t.sol` tests `test_allowedSwapSucceeds` by allowlisting `address(callers[0])` (the direct caller contract), not the router. No test exercises the router-mediated path against a restricted pool, confirming the bypass is untested.

## Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or a private market) is fully bypassed for any user who calls `MetricOmmSimpleRouter`. An unauthorized attacker can execute swaps at oracle-derived prices against LP capital that was deposited under the assumption that only allowlisted counterparties would trade. This directly exposes LP principal to unauthorized extraction — a direct loss of user funds matching the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation

**Medium.** The bypass is reachable whenever (a) a pool deploys `SwapAllowlistExtension` and (b) the pool admin allowlists the router so that legitimate users can access the router's slippage-protection and multi-hop features. Both conditions are the expected production configuration for any allowlisted pool that also wants router compatibility. No privileged access, no malicious setup, and no non-standard token behavior is required — only a standard `exactInputSingle` call from any EOA.

## Recommendation

1. **Forward the real user identity through the swap call.** Add a `swapper` parameter to `IMetricOmmPoolActions.swap` (analogous to `owner` in `addLiquidity`) so the pool can pass the true initiator to extensions. The router would supply `msg.sender` as `swapper`.

2. **Alternatively, gate on a signed identity payload in `extensionData`.** The router already knows the real user; it can embed a verifiable claim in `extensionData` that the extension checks.

3. **Document the current limitation explicitly** so pool admins do not deploy `SwapAllowlistExtension` with the router allowlisted under the false belief that individual-user gating is enforced.

## Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // Alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required so Alice can use the router
4. LPs add liquidity to the pool.

Attack
──────
5. Charlie (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      pool,
           recipient: charlie,
           zeroForOne: true,
           amountIn:  X,
           ...
       })
6. Router calls pool.swap(charlie, true, X, ...) with msg.sender = router.
7. Pool calls _beforeSwap(sender=router, ...).
8. Extension evaluates allowedSwapper[pool][router] → true → passes.
9. Charlie's swap executes at oracle price, draining LP token1.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

**Foundry test plan:**
- Deploy pool with `SwapAllowlistExtension` as `beforeSwap` hook.
- Allowlist only Alice and the router (`setAllowedToSwap`).
- Have Charlie (not allowlisted) call `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
- Assert the swap succeeds (demonstrating the bypass) where a direct `pool.swap()` from Charlie would revert. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
