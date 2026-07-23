### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool receives the **router's address** as `msg.sender`, so the extension checks whether the **router** is allowlisted, not the actual user. If the router is allowlisted on a curated pool (the only way to let allowlisted users use the router), any unprivileged user can bypass the per-user swap gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the `beforeSwap` call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value the pool forwarded: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

This creates an irreconcilable conflict for any curated pool that wants to support router-mediated swaps:

- **If the router is NOT allowlisted**: allowlisted users cannot use the router at all — every router-mediated swap reverts because the extension sees `sender = router`, which is not in the allowlist.
- **If the router IS allowlisted**: the allowlist is completely bypassed — any user can call `exactInputSingle` and the extension sees `sender = router` (allowlisted), so the swap proceeds regardless of who the real user is.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, institutional participants, or protocol-internal actors) loses that guarantee entirely once the router is allowlisted. Any unprivileged address can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`. This drains liquidity reserved for the intended participants, moves the pool cursor in ways that disadvantage them, and breaks any compliance or access-control requirement the pool admin intended to enforce.

---

### Likelihood Explanation

The trigger is a standard public periphery call with no special privileges. The precondition — the router being allowlisted — is the only operationally sensible configuration for a curated pool that wants to support the official router. Pool admins who deploy `SwapAllowlistExtension` and also want users to use the router will inevitably reach this configuration. The bypass requires no flash loans, no price manipulation, and no admin cooperation beyond the initial (reasonable) router allowlisting.

---

### Recommendation

The extension must gate on the **original user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as an additional field in `extensionData` (or a dedicated parameter), and `SwapAllowlistExtension` should decode and check that value when `sender` is a known router.

2. **Check `sender` and `recipient` together**: For router-mediated swaps, the real economic actor is the payer (stored in transient storage by the router). The extension interface could be extended to carry a `payer` field distinct from `sender`.

The simplest near-term fix is to have `SwapAllowlistExtension` check **both** `sender` and `recipient` — if `sender` is an allowlisted router, fall back to checking `recipient` as the effective swapper. This is imperfect but closes the immediate bypass for single-hop swaps.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists alice (KYC'd user): allowedSwapper[pool][alice] = true
  - Pool admin allowlists router (to let alice use it): allowedSwapper[pool][router] = true

Attack (by bob, not allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) → msg.sender inside pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true
  5. Swap executes; bob receives tokens from the curated pool

Result: bob, who is not allowlisted, successfully swaps against the curated pool.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
