Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of real swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates pool swaps to a per-pool allowlist of swapper addresses, but checks `sender` — which is `msg.sender` of the pool's `swap` call — rather than the originating user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address. A pool admin who allowlists the router to support router-mediated swaps for approved users inadvertently grants swap access to every user who calls the router, completely defeating the per-user access control.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as that `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` faithfully forwards this `sender` value to the extension with no modification: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router contract**, not the end user:

```solidity
// MetricOmmSimpleRouter.sol L71-80
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
``` [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist status — rather than the actual user's. There is no mechanism in the call chain to propagate the original end-user identity through the router call.

## Impact Explanation
A pool admin who deploys a `SwapAllowlistExtension`-guarded pool and allowlists the router (the natural operational step to let approved users trade via the standard periphery) loses all per-user access control. Any address — including non-KYC'd or otherwise excluded users — can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the restricted pool. This constitutes broken core pool functionality and an admin-boundary break: the pool admin's configured access policy is silently defeated by the supported periphery path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical swap entry point. Pool admins who want their allowlisted users to use the router must allowlist the router address — this is the expected operational pattern. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, flash loans, or unusual token behavior required.

## Recommendation
Pass the original end-user identity through the call chain so the extension can gate on the real swapper. Two options:

1. **Preferred — propagate `originSender`**: Encode the original `msg.sender` (the actual user) in `extensionData` at the router level, and have `SwapAllowlistExtension.beforeSwap` decode and check it instead of `sender`. Alternatively, add an `originSender` field to the `beforeSwap` hook signature.

2. **Alternative — check `tx.origin`**: Replace `sender` with `tx.origin` inside `SwapAllowlistExtension.beforeSwap`. This is simpler but incompatible with smart-contract swappers and introduces phishing risks.

The `DepositAllowlistExtension` avoids this problem because it gates on `owner` (the position owner supplied explicitly by the caller), not on `msg.sender` of the pool call. The swap allowlist should adopt an equivalent explicit-identity pattern. [5](#0-4) 

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    to allow router-mediated swaps for approved users.
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  - attacker (non-allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: restrictedPool,
          ...
      })
  - Router calls pool.swap(...) with msg.sender = router.
  - Pool calls _beforeSwap(router, ...).
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  - Swap executes successfully for the non-allowlisted attacker.

Result:
  - attacker trades on a pool they were never authorized to access.
  - The SwapAllowlistExtension guard is completely bypassed.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
