### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, making the allowlist bypassable or unusable when the official router is involved — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap(...)` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. This creates an inescapable dilemma for any pool admin who deploys a swap-allowlisted pool: either (a) do not allowlist the router, breaking the ability of legitimate allowlisted users to use the official periphery, or (b) allowlist the router, at which point **any** unprivileged user can bypass the allowlist by routing through the public router contract.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension's `beforeSwap` hook.

`SwapAllowlistExtension.beforeSwap` then checks that first argument (`sender`) against the per-pool allowlist, using `msg.sender` (the pool) as the mapping key: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` on the user's behalf. Inside the pool, `msg.sender` is the **router**, so `sender = router` is what the extension receives and checks. The actual originating user address is never visible to the extension.

The allowlist is keyed `allowedSwapper[pool][sender]`. For router-mediated swaps, `sender` is always the single shared router address. Two outcomes follow:

1. **Router not allowlisted**: Every router-mediated swap reverts with `NotAllowedToSwap`, even for users whose addresses are individually allowlisted. Allowlisted users are forced to call the pool directly, bypassing the official periphery.

2. **Router allowlisted** (the only way to let allowlisted users use the router): `allowedSwapper[pool][router] = true` opens the gate for **every** caller of the router, including addresses the pool admin explicitly never allowlisted. The allowlist is fully neutralised.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (the LP share recipient), which the liquidity adder sets to the originating user: [3](#0-2) 

The swap extension has no equivalent forwarding of the true originator.

---

### Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or compliance-gated participants) can be fully bypassed by any unprivileged user who calls `MetricOmmSimpleRouter`. The bypassing user executes real swaps against the pool's liquidity, extracting token output and imposing token input obligations on the pool exactly as a legitimate allowlisted swapper would. LP principal is at risk because the pool was deployed under the assumption that only vetted counterparties would trade against it.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the canonical, publicly documented swap entrypoint. Any user who reads the protocol documentation will naturally use the router. No special knowledge, flash loan, or privileged access is required. The bypass is a single direct call to a public function on a deployed contract.

---

### Recommendation

The `sender` forwarded to extension hooks must represent the **economic originator** of the swap, not the immediate `msg.sender` of the pool call. Two approaches:

1. **Router-forwarded identity**: Extend the pool's `swap` interface with an explicit `swapper` parameter (distinct from `recipient`). The router populates this with `msg.sender` before calling the pool. The pool passes `swapper` to `_beforeSwap` instead of its own `msg.sender`. The pool must validate that only trusted periphery contracts may supply a `swapper` different from their own address.

2. **Extension-level router awareness**: `SwapAllowlistExtension.beforeSwap` decodes an `actualSwapper` address from `extensionData` when `sender` is a known router, and checks that decoded address. The router must be required to supply and sign this field. This is more fragile but avoids a core interface change.

Option 1 is preferred because it preserves the invariant at the pool level and does not rely on extension-specific payload conventions.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can use the router at all).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).
  - Pool admin adds liquidity.

Attack:
  1. attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       pool, zeroForOne, amountIn, minOut, deadline, attacker, extensionData
     ).
  2. Router calls pool.swap(attacker, zeroForOne, amountIn, priceLimit, callbackData, extensionData).
  3. Pool sets sender = msg.sender = router.
  4. _beforeSwap dispatches to SwapAllowlistExtension.beforeSwap(router, ...).
  5. Extension checks allowedSwapper[pool][router] == true → passes.
  6. Swap executes. attacker receives token output.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; allowlist is bypassed.
``` [2](#0-1) [1](#0-0)

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
