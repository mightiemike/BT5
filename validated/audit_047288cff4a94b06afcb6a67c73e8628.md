### Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any unprivileged caller to bypass the swap allowlist on curated pools - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the allowlist by routing through the same public router.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes that value as the first argument of `IMetricOmmExtensions.beforeSwap`:

```solidity
// ExtensionCalling.sol:160-176
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (
    sender,   // ← still the router address
    ...
))
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original caller:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The pool admin faces an inescapable dilemma:

- **Do not allowlist the router**: allowlisted users cannot use the router at all; they must call `pool.swap()` directly.
- **Allowlist the router**: the check `allowedSwapper[pool][router]` passes for every caller who routes through the router, regardless of whether that caller is on the allowlist. Any unprivileged user bypasses the gate by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`.

### Impact Explanation

A pool deployed with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners) loses that restriction entirely for any user who routes through the public `MetricOmmSimpleRouter`. The attacker can execute swaps at the oracle-anchored bid/ask, draining LP value or front-running protected order flow that the allowlist was meant to prevent. This is a direct loss of LP principal and a broken core pool invariant.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint documented in the protocol. Any pool admin who deploys a curated pool and then allowlists the router (the natural step to make the pool usable via the standard periphery) immediately opens the bypass. The attacker needs no special privilege, no flash loan, and no multi-step setup — a single `exactInputSingle` call suffices.

### Recommendation

The extension must verify the actual end user, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Decode the real sender from `extensionData`**: require the router to ABI-encode the original `msg.sender` into `extensionData` and have the extension decode and check it. The extension should fall back to checking `sender` when `extensionData` is empty (direct pool calls).

2. **Check `sender` and document that the router is incompatible with this extension**: add a NatSpec warning that pools using `SwapAllowlistExtension` must not allowlist any router or intermediary contract, and that allowlisted users must call `pool.swap()` directly. This is a usability restriction but avoids the bypass.

Option 1 is preferable for production use. The router would need a corresponding change to always append `abi.encode(msg.sender)` to `extensionData` before forwarding to the pool.

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin allowlists alice (legitimate user) and the router (to enable router swaps)
  bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  → router calls pool.swap(recipient=bob, ...) with msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  → swap executes; bob receives tokens despite not being allowlisted
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
