### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the allowlist to every user, because the extension cannot distinguish between allowlisted and non-allowlisted users once the router is the gating identity.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by `MetricOmmPool.swap`, which passes `msg.sender` of the pool call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- always the immediate pool caller
    recipient,
    ...
    extensionData
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutput`, `exactOutputSingle`), the router calls `pool.swap(...)` directly. At that point `msg.sender` inside the pool is the router, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants legitimate allowlisted users to be able to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through `MetricOmmSimpleRouter`.

The multi-hop `exactInput` path makes this worse: for every intermediate hop the router is `msg.sender` at the pool, so all hops on all pools in the path are gated only on the router identity.

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional LPs, or whitelisted market makers) loses its curation guarantee entirely once the router is allowlisted. Any unprivileged user can execute swaps on the pool, draining LP value at oracle-derived prices, extracting spread fees, or triggering stop-loss extensions in ways the pool admin did not intend. This is a direct loss of LP principal and a broken core pool invariant (curated access).

### Likelihood Explanation

The pool admin faces an unavoidable dilemma: either allowlist the router (enabling the bypass for all users) or do not allowlist the router (blocking all router-mediated swaps for legitimate users too). Any production deployment that wants to support the standard periphery router while also enforcing an allowlist will naturally allowlist the router, triggering the bypass. The attacker needs no special privilege — a single public call to `MetricOmmSimpleRouter.exactInputSingle` suffices.

### Recommendation

The extension must receive and check the originating user identity, not the immediate pool caller. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `sender` against the router and fall back to a per-router user registry**: The extension recognises known router addresses and requires a separate proof of the originating user.
3. **Architectural fix**: `MetricOmmPool.swap` should accept and forward an explicit `originator` address (distinct from `msg.sender`) so extensions can gate on the economic actor rather than the transport layer.

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only allowed swapper
  allowedSwapper[P][router] = true  // admin allowlists router so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})

  Router calls:
    P.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    // msg.sender inside P = router

  Pool calls:
    E.beforeSwap(router, recipient, ...)
    // checks allowedSwapper[P][router] == true  ✓
    // bob's identity is never checked

Result:
  bob executes a swap on a curated pool he is not allowed to access.
  The allowlist is completely bypassed.
```

--- [1](#0-0) 

The extension checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate pool caller, not the originating user. [2](#0-1) 

`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`, so the extension always sees the router address on router-mediated swaps. [3](#0-2) 

`exactInputSingle` calls `pool.swap` directly from the router, making the router the `msg.sender` at the pool level. [4](#0-3) 

`exactInput` multi-hop path also calls each pool's `swap` from the router, so every hop in a multi-pool path is gated only on the router identity. [5](#0-4) 

`_beforeSwap` in `ExtensionCalling` encodes `sender` (the pool's `msg.sender`) into the ABI-encoded call forwarded to the extension, confirming the router address is what the extension receives.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
