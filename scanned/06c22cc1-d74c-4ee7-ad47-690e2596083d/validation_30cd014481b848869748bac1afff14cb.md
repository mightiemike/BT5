### Title
`SwapAllowlistExtension` gates the router address instead of the originating swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through the public `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. A pool admin who allowlists the router (a natural step to let legitimate users reach the pool via the router) inadvertently opens the pool to every user who routes through it, because the allowlist key becomes the router address rather than the individual swapper.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates the allowlist against that `sender` value, using `msg.sender` (the pool) as the mapping key: [3](#0-2) 

When a user calls the pool directly, `sender = user` and the check is correct. When the same user routes through `MetricOmmSimpleRouter`, `sender = router`. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router address — a necessary step for any allowlisted user to reach the pool via the router — the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted.

The `setAllowedToSwap` setter that the admin uses to configure the allowlist operates on individual addresses with no mechanism to distinguish "this address is a pass-through router" from "this address is a permitted end user": [4](#0-3) 

The pool admin therefore faces an impossible choice: either block the router entirely (preventing allowlisted users from using it) or allowlist the router (silently opening the pool to all users). There is no configuration that simultaneously allows legitimate router usage and enforces per-user gating.

---

### Impact Explanation

A non-allowlisted user can swap on a curated pool — bypassing the intended access-control boundary — by routing through the public `MetricOmmSimpleRouter`. Curated pools are typically deployed to restrict counterparties (e.g., KYC-gated, institutional-only, or strategy-specific pools). Unauthorized swaps against such pools can drain LP-owned inventory at oracle-quoted prices, constituting a direct loss of LP principal. This matches the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" criteria.

---

### Likelihood Explanation

The bypass requires the router to be allowlisted. Any pool that intends to support router-based swaps for its allowlisted users must allowlist the router, making this configuration common in practice. The triggering action (routing through the public `MetricOmmSimpleRouter`) is fully unprivileged and requires no special access. The pool admin's allowlisting of the router is a reasonable, non-malicious configuration choice whose side-effect (universal bypass) is non-obvious from the API.

---

### Recommendation

The allowlist must be keyed on the economically relevant actor — the end user — not the immediate caller. Two viable approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` as an explicit `sender` field inside `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that field when the immediate caller is a known router.

2. **Sender-override parameter**: Add an optional `effectiveSender` parameter to the pool's `swap()` interface that trusted routers can populate; the pool passes this to extensions instead of `msg.sender`. The pool must verify the caller is an authorized router before accepting a non-self `effectiveSender`.

Either approach must ensure the override path is itself gated so that arbitrary callers cannot spoof an allowlisted identity.

---

### Proof of Concept

```
Setup
─────
1. Pool P is deployed with SwapAllowlistExtension E configured.
2. Admin calls E.setAllowedToSwap(P, alice, true)   // alice is KYC'd
3. Admin calls E.setAllowedToSwap(P, router, true)  // router allowlisted so alice can use it
4. bob is NOT allowlisted.

Attack
──────
5. bob calls MetricOmmSimpleRouter.exactInput(... pool=P ...)
   → router calls P.swap(recipient=bob, ...)
   → pool calls _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension checks allowedSwapper[P][router] == true  ✓
   → swap executes; bob receives token output from LP inventory

Result
──────
bob, a non-allowlisted user, successfully swaps against a curated pool,
bypassing the per-user allowlist entirely.
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
