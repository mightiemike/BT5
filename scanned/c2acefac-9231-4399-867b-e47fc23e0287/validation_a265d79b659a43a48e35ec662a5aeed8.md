### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter: Router Address Replaces User Identity in Allowlist Check — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap` function passes `msg.sender` (the router's address) as `sender` to the extension. If the pool admin allowlists the router — a natural action to let allowlisted users access the router — the allowlist check degenerates to a single router-address check, and every user of the public router bypasses the per-user gate entirely.

---

### Finding Description

**Root cause — wrong identity in the allowlist check:**

`MetricOmmPool.swap` captures `msg.sender` and forwards it as `sender` to every `beforeSwap` extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so `sender` = router address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The bypass path:**

A pool admin who wants allowlisted users to access the router must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every caller of the router — allowlisted or not. The per-user gate is completely collapsed into a single router-address gate.

**Analog to the CDSLib invariant break:**

In CDSLib, `totalCdsDepositedAmount` is decremented by the *returned* amount (which embeds the loss) instead of the *original deposited* amount, so the denominator used in all subsequent cumulative calculations is wrong. Here, the *identity* used in the allowlist check is the router's address (a proxy) instead of the actual user's address (the intended subject), so the access-control predicate evaluates against the wrong principal. Both bugs substitute a derived/proxy value for the true value in a critical invariant check, breaking the invariant for all subsequent operations that depend on it.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd institutional traders) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, extract value from the pool via adverse selection, and front-run allowlisted LPs — all in a pool the admin believed was access-controlled. This is a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a configured security guard.

---

### Likelihood Explanation

The scenario is reachable under normal, non-malicious conditions:

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict trading.
2. Admin allowlists `MetricOmmSimpleRouter` so that allowlisted users can enjoy the router's UX (slippage caps, multicall, etc.) — a standard operational step.
3. Any public user calls `MetricOmmSimpleRouter` targeting that pool.
4. The extension sees `sender = router`, which is allowlisted → swap succeeds.

No privileged attacker role, no malicious setup, and no non-standard tokens are required. The trigger is a routine admin action with an unintended consequence baked into the code.

---

### Recommendation

The extension must check the *end-user* identity, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** `MetricOmmSimpleRouter` should forward `msg.sender` as an authenticated field in `extensionData`, and `SwapAllowlistExtension` should decode and verify it (with the pool confirming the router is a trusted forwarder).

2. **Check `sender` only when it is the terminal actor.** Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this at the factory level (e.g., reject pool configurations that pair a swap allowlist with a public router extension slot).

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; configure beforeSwap order to call it.
2. Admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
3. Admin calls setAllowedToSwap(pool, alice, true)    // allowlist alice directly
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(pool, ...)
5. Router calls pool.swap(...) with msg.sender = router
6. _beforeSwap(sender=router, ...) → allowedSwapper[pool][router] == true → passes
7. Bob's swap executes on a pool he was never meant to access.

Invariant broken: allowedSwapper[pool][bob] == false, yet Bob's swap settles.
``` [3](#0-2) [4](#0-3) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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
