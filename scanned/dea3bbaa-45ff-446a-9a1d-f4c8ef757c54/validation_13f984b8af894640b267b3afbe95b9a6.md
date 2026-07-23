### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. Because `MetricOmmPool.swap` sets `sender = msg.sender` (the immediate caller), any swap routed through `MetricOmmSimpleRouter` presents the router's address as `sender` instead of the actual user's address. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those not individually allowlisted — can bypass the curated-pool gate by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool forwarded: [3](#0-2) 

When a user calls the pool directly the chain is `user → pool.swap()`, so `sender = user` and the check is correct. When the user routes through `MetricOmmSimpleRouter`, the chain is `user → router → pool.swap()`, so `sender = router`. The extension then evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This creates an irreconcilable asymmetry identical in structure to the external report's directional-scaling bug: the guard is applied to the correct actor on one entry path (direct call) and to the wrong actor on the other (router-mediated call).

A pool admin who wants allowlisted users to be able to use the router must add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller regardless of whether that individual user is on the allowlist, because the extension never sees the user's address at all. [4](#0-3) 

---

### Impact Explanation

Any user not individually allowlisted can trade on a curated pool by routing through `MetricOmmSimpleRouter` when the router is allowlisted. This is a direct bypass of the pool's access-control policy. Curated pools are typically deployed to restrict trading to known counterparties (e.g., KYC'd users, protocol-owned addresses, or specific market makers). Unauthorized swaps drain LP-owned liquidity and generate fees for the protocol from actors the pool was explicitly designed to exclude. This constitutes a broken core pool functionality and direct loss of LP principal through unauthorized execution.

---

### Likelihood Explanation

The scenario is highly likely in practice:

1. `MetricOmmSimpleRouter` is the canonical user-facing swap entry point documented in the protocol.
2. A pool admin who deploys a curated pool and wants allowlisted users to use the standard router **must** allowlist the router — there is no other mechanism.
3. Once the router is allowlisted, the bypass is immediately available to any address with no additional preconditions, no privileged access, and no special token behavior. [5](#0-4) 

---

### Recommendation

The extension must resolve the actual end-user address rather than trusting the `sender` argument when the immediate caller is a known periphery contract. Two sound approaches:

1. **Pass-through identity**: Require the router to forward the originating user address in `extensionData`, and have `SwapAllowlistExtension` decode and check that address when `sender` is a recognized router.
2. **Transient-storage identity**: Have the router write the originating user into transient storage before calling the pool, and have the extension read it from there.
3. **Direct-only policy**: Document that allowlisted pools must not allowlist any router or intermediary, and enforce this at the factory level by rejecting extension configurations that combine `SwapAllowlistExtension` with a router allowlist entry.

The root fix is that `allowedSwapper` must be keyed on the economically responsible actor (the end user), not the immediate `msg.sender` of the pool call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured (allowAllSwappers = false).
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router usage
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  - bob is NOT allowlisted.

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInput(..., pool, ...)
  2. Router calls pool.swap(recipient=bob, ...) — msg.sender in pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true  ✓
  5. Swap executes successfully for bob despite bob not being allowlisted.

Direct call check (for comparison):
  1. bob calls pool.swap(...) directly
  2. Pool calls _beforeSwap(sender=bob, ...)
  3. SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → revert ✓

Result: the allowlist is enforced on the direct path but silently bypassed on the
router path, exactly mirroring the external report's asymmetric-scaling pattern
where the guard is correctly applied in one direction and misapplied in the other.
``` [3](#0-2) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
