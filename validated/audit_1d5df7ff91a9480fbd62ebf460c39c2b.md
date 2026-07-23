Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the EOA initiator, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the EOA. Because the router must be allowlisted for any router-mediated swap to succeed on a restricted pool, the allowlist check degenerates to `allowedSwapper[pool][router]`, which passes for every caller of the router regardless of whether that individual user is permitted.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that `sender` value verbatim to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with no user-identity forwarding — the pool sees the router as `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165). [5](#0-4) 

**Root cause:** The allowlist is keyed on `(pool → router)`, not `(pool → EOA)`. For router-mediated swaps to function at all on an allowlisted pool, the admin must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router]` passes for every caller of the router, regardless of whether that individual user is permitted. No existing guard in the extension, pool, or router prevents this.

## Impact Explanation
A pool deployer configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd users, whitelisted market makers). Any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle()` targeting the restricted pool and execute a full swap, receiving output tokens from the pool's LP reserves. The allowlist guard — the only on-chain enforcement of this policy — is completely nullified for all router-mediated paths. This constitutes broken core pool functionality and direct loss of LP assets, qualifying as **High** severity under the allowed impact gate.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the canonical, publicly deployed periphery swap entry point.
- Any pool that wants to support router-mediated swaps must allowlist the router; this is the normal operational expectation.
- No special privileges, flash loans, or unusual token behavior are required — a standard `exactInputSingle` call suffices.
- The bypass is unconditional once the router is allowlisted: every block, every user.

## Recommendation
The extension must check the economically relevant actor — the EOA who initiated the transaction — not the intermediate contract. Two viable approaches:

1. **Pass the original initiator through the pool.** Add an optional `originator` field to the swap parameters that the router populates with `msg.sender` before calling the pool, and have the pool forward it to extensions alongside `sender`.
2. **Check `tx.origin` as a fallback.** When `sender` is a known periphery contract, fall back to `tx.origin`. This is acceptable in an allowlist context where the goal is to gate the human initiator, not the contract intermediary.
3. **Document and enforce that the router must never be allowlisted.** Add a factory-level check that rejects allowlisting of the canonical router address, forcing all permitted users to call the pool directly.

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension
  allowedSwapper[P][alice] = true          // only alice is permitted
  allowedSwapper[P][router] = true         // required for router to work

Attack (executed by bob, who is NOT allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: P,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls P.swap(bob, true, X, ...) — msg.sender = router
  3. Pool calls _beforeSwap(router, bob, ...)
  4. Extension checks allowedSwapper[P][router] → true ✓
  5. Swap executes; bob receives output tokens from LP reserves

Result: bob, an unpermissioned user, successfully swaps on a curated pool.
The allowlist guard is completely bypassed.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
