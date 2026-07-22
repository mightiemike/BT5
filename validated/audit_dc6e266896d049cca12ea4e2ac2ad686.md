### Title
SwapAllowlistExtension gates on the intermediary router address instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool's `swap` call. If the router is allowlisted — which is the only way to permit router-mediated swaps on a curated pool — every user, including explicitly disallowed ones, bypasses the individual allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension:

```solidity
// ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that first argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

`msg.sender` of that `pool.swap` call is the **router contract**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an impossible choice:
- **Allowlist the router** → every user, including banned ones, can swap by routing through it.
- **Do not allowlist the router** → individually allowlisted users cannot use the router at all.

The analog to the external report's bug class is exact: just as `timestampAt` uses the byte-length of the DER field as a proxy for the ASN.1 tag type (an unreliable discriminator), `SwapAllowlistExtension` uses `sender` (the router address) as a proxy for the actual user identity — an unreliable discriminator that collapses all end-users into a single identity the moment they route through the periphery.

---

### Impact Explanation

Any user who is explicitly blocked on a curated pool can bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). The pool receives and settles the swap normally; the allowlist guard never sees the real user. This is a direct, unconditional policy bypass on every pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router to support normal periphery usage. LP funds are exposed to counterparties the pool admin explicitly excluded.

---

### Likelihood Explanation

The router is a public, permissionless contract. Any disallowed user can call it without any special access. The only precondition is that the pool admin has allowlisted the router — a step that is operationally required for the pool to be usable through the standard periphery. No privileged action, no malicious setup, and no non-standard token is needed.

---

### Recommendation

The allowlist must be keyed to the economically relevant actor, not the intermediary. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router signs or encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted router or a signature scheme.
2. **Check `sender` only for direct pool calls; require a separate allowlist entry for the router that encodes the real user**: The extension reads the actual user from a verified field in `extensionData` when `sender` is a known router, and falls back to `sender` for direct calls.
3. **Document that the extension is incompatible with the router** and enforce this at the factory level by rejecting pool configurations that combine `SwapAllowlistExtension` with any router-facing extension order.

---

### Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured in BEFORE_SWAP_ORDER.
2. Pool admin calls E.setAllowedToSwap(P, router, true)   // router allowlisted for normal use
3. Pool admin calls E.setAllowedToSwap(P, alice, true)    // alice is individually allowed
4. Pool admin does NOT call E.setAllowedToSwap(P, bob, true) // bob is disallowed

5. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
   → router calls P.swap(recipient, zeroForOne, amount, limit, "", extensionData)
   → msg.sender of P.swap = router
   → P calls _beforeSwap(router, ...)
   → E.beforeSwap(sender=router, ...) checks allowedSwapper[P][router] == true
   → guard passes; bob's swap executes on the curated pool
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
