### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any Caller to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end-user. A pool admin who allowlists the router so that legitimate users can trade through it inadvertently opens the allowlist to every caller: any unprivileged user can route through the router and pass the guard unchecked.

---

### Finding Description

`ExtensionCalling._beforeSwap` forwards `sender` to every configured extension: [1](#0-0) 

The value bound to `sender` at that call site is `msg.sender` of `MetricOmmPool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) calls `pool.swap(...)`, the pool sees the router as `msg.sender`, so `sender = router_address` is what reaches the extension.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```
allowedSwapper[pool][sender]   // sender == router, not the end-user
```

A pool admin who wants to restrict swaps to a curated set of users faces an impossible choice:

| Admin configuration | Outcome |
|---|---|
| Allowlist the router so legitimate users can trade through it | Every unprivileged user can bypass the allowlist by routing through the router |
| Do not allowlist the router | Allowlisted users cannot use the router; the primary public swap path is broken for them |

The first branch is the exploitable one: once the router is allowlisted (the natural operational choice), the `SwapAllowlistExtension` guard is completely neutralised for all callers.

The analog to the external report is exact: just as `_claimablePerPeriod` returns 0 for the first period because `poolTotalVotesPerPeriod` is structurally zero (no votes can land in the current period), `SwapAllowlistExtension` structurally checks the wrong identity (the intermediary, not the user) whenever the public swap path goes through the router. In both cases the guard silently passes when it should block, and the condition is not an edge case — it is the normal operating path. [2](#0-1) [3](#0-2) 

---

### Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to restrict trading to a curated set of counterparties (e.g., KYC-verified users, institutional LPs, or whitelisted market makers). Once the router is allowlisted, any unprivileged address can execute swaps against the pool. Consequences include:

- **Unauthorized extraction of LP value**: non-allowlisted actors can trade against the pool's oracle-anchored prices, causing adverse-selection losses to LPs who deposited under the assumption that only trusted counterparties could trade.
- **Broken access-control invariant**: the core pool functionality (restricted swap) is rendered non-functional; the guard passes for every caller regardless of their allowlist status.

This satisfies the "Admin-boundary break … bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact categories.

---

### Likelihood Explanation

- The router is the primary public entry point for swaps; pool admins who deploy a `SwapAllowlistExtension` pool and want their allowlisted users to trade through the standard UI will allowlist the router as a matter of course.
- No special privilege, flash loan, or unusual token behaviour is required. Any EOA can call `MetricOmmSimpleRouter.exactInputSingle`.
- The bypass is deterministic and repeatable on every block.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **originating user**, not the intermediary. Two sound approaches:

1. **Pass the original caller through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as an explicit `originalSender` field inside `extensionData`, and `SwapAllowlistExtension` should decode and check that value instead of `sender`.
2. **Check `recipient` as a proxy for the beneficiary** (weaker, but avoids router changes): if the pool's design guarantees that `recipient` is always the end-user, the extension can gate on `recipient`. This is fragile if recipient can be set arbitrarily.

Option 1 is the correct fix. The extension should decode the original caller from `extensionData` and revert if that address is not in `allowedSwapper[pool]`.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured as beforeSwap hook.

2. Pool admin calls SwapAllowlistExtension.setAllowedSwapper(pool, router, true)
   — necessary so that allowlisted users can trade through the standard router UI.

3. Pool admin calls SwapAllowlistExtension.setAllowedSwapper(pool, alice, true)
   — alice is the only intended trader.

4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle(
           pool, zeroForOne=true, amountIn=X, minOut=0, priceLimit, extensionData
       )

5. Router calls pool.swap(...) — pool sees msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...) → ExtensionCalling dispatches to
   SwapAllowlistExtension.beforeSwap(sender=router, ...).

7. Extension checks allowedSwapper[pool][router] == true → passes.

8. Bob's swap executes successfully against the restricted pool.
   Alice's exclusive access guarantee is violated; LPs suffer adverse-selection
   losses from an actor the pool was designed to exclude.
``` [1](#0-0) [3](#0-2)

### Citations

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
