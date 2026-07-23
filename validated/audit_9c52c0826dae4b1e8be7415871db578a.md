### Title
`SwapAllowlistExtension` gates the router address instead of the actual user when swaps are routed through `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. If the router is allowlisted (the natural admin action to enable router-mediated swaps), every non-allowlisted user can bypass the per-user gate by calling through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's own `swap` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter` (the supported periphery), the router calls `pool.swap(...)`. The pool's `msg.sender` is the router, so `sender` = router. The allowlist check becomes:

```
allowedSwapper[pool][router]
```

A pool admin who wants legitimate users to use the router must allowlist the router address. Once the router is allowlisted, **any** address can call the router and the extension will pass them through, because the check never inspects the actual end-user — only the router.

The extension's own documentation and the research target both state the invariant: *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* This invariant is broken.

---

### Impact Explanation

**Direct loss of curation / unauthorized fund access.** A curated pool using `SwapAllowlistExtension` is designed to restrict which counterparties can trade against LP funds. Once the router is allowlisted (the only way to allow any router-mediated swap), the allowlist is effectively nullified for all users. Non-allowlisted users can drain LP-provided liquidity at oracle prices, causing direct loss of LP principal. This matches the "High direct loss or curation failure if disallowed users can still trade or deposit" impact gate.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the standard, documented periphery for swaps. Any pool admin deploying a curated pool with `SwapAllowlistExtension` who also wants users to be able to use the router (the normal UX path) will allowlist the router. The bypass requires no special privilege — any user can call the router. The attacker needs only to know the router address and the pool address.

---

### Recommendation

The extension must resolve the actual end-user identity, not the intermediary. Two options:

1. **Pass the real user through the router.** Have `MetricOmmSimpleRouter` accept a `swapper` parameter and forward it as `extensionData`; the extension decodes and checks it. This requires the router to be trusted to not forge identities.

2. **Check `recipient` instead of `sender` for the allowlist.** If the pool's design intent is that the *recipient* of swap proceeds is the gated party, check `recipient` (the second argument to `beforeSwap`). This is only correct if recipient == end-user, which holds for direct swaps but may not hold for all router configurations.

3. **Reject router-mediated swaps entirely.** Require `sender == tx.origin` or maintain a registry of trusted forwarders that attest to the real caller identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT allowlist attacker address.

Attack:
  1. attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
  2. Router calls pool.swap(recipient=attacker, ...)
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true → passes
  5. Swap executes; attacker receives tokens from LP funds.

Result:
  - Attacker bypassed the per-user allowlist.
  - LP funds transferred to non-allowlisted counterparty.
  - allowedSwapper[pool][attacker] was never set to true.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
