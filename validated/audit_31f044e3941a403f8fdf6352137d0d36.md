### Title
`SwapAllowlistExtension` Gates Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps), every user who calls through the router bypasses the per-user allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the call chain is:

```
User → MetricOmmSimpleRouter.exactInputSingle() → MetricOmmPool.swap(msg.sender = Router)
```

So `sender` = `address(router)`. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for the pool admin:
- **Router not allowlisted:** Legitimate allowlisted users cannot use the router at all — their swaps revert because the router is not on the list.
- **Router allowlisted:** Every user, including those explicitly excluded from the allowlist, can bypass the gate by routing through the router.

The analog to M-06 is exact: just as `_from == _recipient` caused the Unlock protocol to apply state changes to the wrong identity, here the router address substitutes for the user identity in the allowlist check, making the guard apply to the wrong actor with fund-impacting consequences.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` or `MetricOmmSimpleRouter.exactInput`. The allowlist — the sole access-control mechanism for swap gating — provides no protection once the router is allowlisted. Any user can drain pool liquidity at oracle-derived prices, defeating the pool admin's intended access policy.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, publicly deployed periphery entry point for all swaps.
- Pool admins who deploy `SwapAllowlistExtension` and want legitimate users to use the router must allowlist the router address; there is no other path.
- Once the router is allowlisted, the bypass requires zero privilege — any EOA or contract can call `router.exactInputSingle()`.
- No special token behavior, malicious setup, or admin action is required from the attacker.

---

### Recommendation

The allowlist check must gate the **economic actor** (the end user), not the intermediary. Two complementary fixes:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` forward the real user address as a dedicated field in `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when present.

2. **Alternatively, check `recipient` instead of `sender` when the pool is called by a known router.** This is less clean but avoids changes to the router ABI.

The cleanest fix is option 1: the router encodes `msg.sender` into `extensionData`, and the extension decodes it as the authoritative swapper identity, falling back to `sender` for direct pool calls.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
  - allowedSwapper[pool][alice] = true   (alice is the only allowed swapper)
  - allowedSwapper[pool][bob]   = false  (bob is explicitly excluded)
  - allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, ..., recipient: bob}).
  2. Router calls pool.swap(recipient=bob, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
  5. Swap executes; bob receives tokens at oracle price.

Result:
  bob, an explicitly excluded address, successfully swaps against the pool,
  bypassing the allowlist entirely. The pool admin's access policy is nullified.
```

**Relevant code locations:**

`MetricOmmPool.swap` passes `msg.sender` as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the allowlist: [3](#0-2)

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
