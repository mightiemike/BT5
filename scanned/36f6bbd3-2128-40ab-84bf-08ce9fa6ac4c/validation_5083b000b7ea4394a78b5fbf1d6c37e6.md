### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the **router's address**, not the end user's address. If the pool admin allowlists the router to enable router-based swaps for curated users, every user — including non-allowlisted ones — can bypass the per-user swap allowlist by calling through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the immediate pool caller) is in the allowlist keyed by `msg.sender` (the pool): [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, `msg.sender` seen by the pool is the **router contract**, so `sender` forwarded to the extension is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

This creates an irresolvable dilemma for any pool admin who configures this extension:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-based swaps revert — even for allowlisted users |
| Router **allowlisted** | All users (including non-allowlisted) can swap through the router — allowlist is fully bypassed |

There is no configuration that achieves "only allowlisted users may swap, and they may use the router."

Note also that `SwapAllowlistExtension.beforeSwap` drops the `onlyPool` modifier that `BaseMetricExtension` applies to its default stub: [4](#0-3) 

While calling the extension directly does not produce a bypass (the `msg.sender`-keyed allowlist would be empty for any non-pool caller), the missing guard is a defence-in-depth gap that widens the attack surface.

The same structural flaw exists in `DepositAllowlistExtension.beforeAddLiquidity`, which also overrides the base without `onlyPool`: [5](#0-4) 

---

### Impact Explanation

A non-allowlisted user can execute swaps on a curated pool that the pool admin intended to restrict. Depending on the pool's purpose (e.g., institutional-only, KYC-gated, or rate-limited liquidity), this allows unauthorized price-taking, fee extraction from LPs, or violation of regulatory/compliance constraints. The pool's LP assets are directly exposed to trades the admin explicitly prohibited.

---

### Likelihood Explanation

Any pool admin who deploys a `SwapAllowlistExtension` and also wants users to be able to swap through the standard `MetricOmmSimpleRouter` will naturally allowlist the router. This is the expected operational configuration; the admin has no other way to support router-based swaps. The bypass is therefore triggered by a routine, well-motivated admin action, not an exotic misconfiguration.

---

### Recommendation

The extension must check the **economic actor** — the end user — not the immediate pool caller. Two complementary fixes:

1. **Pass the original user through the router.** Have `MetricOmmSimpleRouter` supply the end user's address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of (or in addition to) `sender`.

2. **Add `onlyPool` to both overrides.** Restore the `onlyPool` modifier on `SwapAllowlistExtension.beforeSwap` and `DepositAllowlistExtension.beforeAddLiquidity` so that only a registered pool can invoke the guard, matching the pattern in `BaseMetricExtension`.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack
──────
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.swap(pool, ...)
5. Router calls pool.swap(recipient, ...) — msg.sender seen by pool = router
6. Pool calls _beforeSwap(router, ...)
7. Extension evaluates: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes on the curated pool despite not being allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
