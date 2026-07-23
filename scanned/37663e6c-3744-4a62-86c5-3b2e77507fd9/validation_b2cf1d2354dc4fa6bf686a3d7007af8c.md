### Title
SwapAllowlistExtension checks router address as `sender` instead of the actual user, allowing non-allowlisted users to bypass swap restrictions via MetricOmmSimpleRouter — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension.beforeSwap` hook gates swaps by checking `sender`, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. If the router is allowlisted (a natural configuration for pools that want to support router-mediated swaps for allowlisted users), any non-allowlisted user can bypass the swap allowlist entirely by routing through the router.

---

### Finding Description

In `MetricOmmPool.swap()`, `msg.sender` is forwarded as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- this is the router when called via MetricOmmSimpleRouter
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

`ExtensionCalling._beforeSwap` then encodes and dispatches this `sender` to the configured extension:

```solidity
// ExtensionCalling.sol
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
         packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
    )
);
```

The `SwapAllowlistExtension.beforeSwap` performs an `allowedSwapper` lookup keyed by `(pool, sender)`. When the call originates from `MetricOmmSimpleRouter.exact*()`, `sender` is the router's address, not the originating user's address. The extension has no visibility into who called the router.

This creates an irresolvable dilemma for the pool admin:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | ❌ Blocked (router not in list) | ❌ Blocked |
| Yes | ✅ Passes | ✅ **Bypasses allowlist** |

If the admin allowlists the router (the only way to let allowlisted users use the router), every non-allowlisted user can also swap by routing through the same public router contract.

---

### Impact Explanation

Non-allowlisted users gain unrestricted swap access to pools that are intended to be restricted (e.g., private institutional pools, pools with KYC requirements, or pools gating access to specific market makers). These users can execute swaps at oracle-anchored prices, extracting value from LPs who deposited under the assumption that only vetted counterparties would trade against them. The pool admin's core access-control invariant is silently broken with no on-chain indication of the bypass.

This is an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses an admin-configured allowlist guard.

---

### Likelihood Explanation

Medium. The bypass requires the router to be allowlisted, which is a natural and expected configuration for any pool that wants allowlisted users to be able to use the standard periphery router. A pool admin who allowlists specific users and also allowlists the router (to give those users router access) will unknowingly open the pool to all users. The router is a public, permissionless contract, so any user can call it.

---

### Recommendation

The `SwapAllowlistExtension` should not rely solely on `sender` for identity. Two options:

1. **Pass the original caller in `extensionData`**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have the extension decode and check it when `sender` is a known router address.
2. **Check both `sender` and a caller field**: Extend the allowlist to support an "operator" model where the extension checks the actual economic actor (the router's caller) rather than the intermediary.

The pool interface should also document that `sender` is the direct caller of `pool.swap()`, not necessarily the end user, so extension authors are aware of this identity gap.

---

### Proof of Concept

```
1. Pool admin deploys MetricOmmPool with SwapAllowlistExtension configured.
2. Admin calls SwapAllowlistExtension.setAllowedSwapper(pool, userA, true)
   — only userA is intended to swap.
3. Admin calls SwapAllowlistExtension.setAllowedSwapper(pool, router, true)
   — to allow userA to use MetricOmmSimpleRouter.
4. Non-allowlisted userB calls MetricOmmSimpleRouter.exactInput(...)
   targeting the restricted pool.
5. Router calls pool.swap(...) with msg.sender = router.
6. pool._beforeSwap(sender=router, ...) is dispatched to SwapAllowlistExtension.
7. Extension checks allowedSwapper[pool][router] == true → PASSES.
8. userB's swap executes at oracle price against LP funds.
9. userB was never allowlisted; the guard was silently bypassed.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
