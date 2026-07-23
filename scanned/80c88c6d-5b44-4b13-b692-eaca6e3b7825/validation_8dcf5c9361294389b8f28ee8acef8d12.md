### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (which is required for any router-based swap to succeed), every user — including those not individually allowlisted — can bypass the guard by routing through the router.

---

### Finding Description

**Hook plumbing — what `sender` the pool passes to the extension:**

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
    msg.sender,   // ← immediate caller of pool.swap(), not the end user
    recipient,
    ...
    extensionData
);
```

**What `SwapAllowlistExtension` checks:**

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

**What the router passes:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol lines 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is therefore the **router address**, not `params.recipient` or the original `msg.sender` of `exactInputSingle`. The extension receives `sender = address(router)`.

**The bypass:**

For any router-based swap to work at all on an allowlisted pool, the pool admin must add the router to `allowedSwapper[pool][router]`. Once that entry exists, the check `allowedSwapper[msg.sender][sender]` passes for every user who routes through the router — regardless of whether that individual user is allowlisted. The per-user allowlist is completely bypassed.

The same structural issue applies to multi-hop `exactInput` for intermediate hops, where `sender` becomes `address(this)` (the router itself):

```solidity
// MetricOmmSimpleRouter.sol line 103
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
```

---

### Impact Explanation

A pool deployer uses `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., a private market-making pool, a KYC-gated pool, or a pool that should only accept flow from trusted integrators). The intended invariant is: only addresses in `allowedSwapper[pool]` may swap.

Because the check resolves to the router address rather than the end user, any unpermissioned user can:
1. Call `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
2. The extension sees `sender = router`, which is allowlisted.
3. The swap executes, bypassing the per-user gate entirely.

Consequences:
- Unauthorized toxic flow reaches LPs who relied on the allowlist for protection, causing direct LP principal loss.
- The pool's intended access-control invariant is silently broken with no on-chain signal.

This matches the **Allowlist path** pivot: "deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router, multicall, callbacks, owner/salt separation, or alternate pool action."

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract, not a mock.
- `MetricOmmSimpleRouter` is the canonical swap entry point; any real deployment will have the router allowlisted on pools that use this extension.
- No special privileges are required: any EOA can call `exactInputSingle` on the router.
- The bypass is automatic and requires zero additional setup beyond the normal router allowlist entry.

---

### Recommendation

The extension must verify the **end user**, not the immediate caller. Two options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.

2. **Check `recipient` instead of `sender`**: If the pool's `recipient` reliably identifies the beneficiary, gate on that. However, `recipient` can be a third-party address, so this is semantically different.

3. **Preferred — add an `originator` field to the extension interface**: The pool passes both `msg.sender` (immediate caller) and a separately supplied `originator` address. The router sets `originator = msg.sender` before calling the pool. The extension checks `originator`.

The cleanest fix is option 3, which requires a coordinated change to `IMetricOmmExtensions.beforeSwap`, `MetricOmmPool.swap`, and `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Pool P uses SwapAllowlistExtension E.
  - Pool admin calls E.setAllowedToSwap(P, router, true)   // router must be allowed for any router swap
  - Pool admin does NOT call E.setAllowedToSwap(P, attacker, true)

Attack:
  1. attacker calls router.exactInputSingle({pool: P, ...})
  2. router calls P.swap(recipient, ...)
  3. P calls E.beforeSwap(sender=router, ...)
  4. E checks: allowedSwapper[P][router] == true  → passes
  5. Swap executes; attacker receives output tokens.

Result:
  - attacker, who is NOT in the allowlist, successfully swaps against the restricted pool.
  - LPs absorb the flow the allowlist was designed to exclude.
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
