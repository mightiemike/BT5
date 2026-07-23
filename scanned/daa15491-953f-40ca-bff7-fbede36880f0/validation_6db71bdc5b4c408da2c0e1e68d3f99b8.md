### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Original User, Enabling Allowlist Bypass via the Official Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  zeroForOne,
  amountSpecified,
  priceLimitX64,
  packedSlot0Initial,
  bidPriceX64,
  askPriceX64,
  extensionData
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` (the first parameter) is allowlisted for the calling pool (`msg.sender` of the extension call = the pool):

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
``` [3](#0-2) 

So `msg.sender` inside `pool.swap()` is the **router address**, and `sender` forwarded to the extension is the **router address**, not the original EOA. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Does **not** allowlist the router | Allowlisted users cannot use the router at all (broken UX) |
| **Does** allowlist the router | Every user — including non-allowlisted ones — can swap through the router (allowlist bypassed) |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., to protect LPs from informed flow or MEV) cannot enforce that restriction when the official router is involved. Any user can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput` and trade against the pool's liquidity, directly exposing LP principal to unrestricted adverse selection. This is a direct loss-of-LP-funds impact above Sherlock medium thresholds.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for EOAs; pool admins are expected to allowlist it.
- The admin has no on-chain signal that allowlisting the router opens the pool to all users — the allowlist UI/admin flow gives no warning.
- Any non-allowlisted user can exploit this with a single standard router call; no special privileges or setup required.

---

### Recommendation

Pass the **original user** through the extension call chain rather than the immediate `msg.sender`. Two concrete options:

1. **Add a `payer`/`originator` field** to the `beforeSwap` hook signature and have the router forward `msg.sender` explicitly (analogous to how `addLiquidity` separates `sender` from `owner`).

2. **Check `tx.origin` as a fallback** — less clean but immediately effective for EOA-only pools.

The cleanest fix is option 1: extend `IMetricOmmExtensions.beforeSwap` with an `originator` parameter, have `MetricOmmPool.swap()` accept and forward it, and have the router pass `msg.sender` as `originator`. `SwapAllowlistExtension` then gates on `originator` instead of `sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
  - Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router so userA can use it

Attack:
  - userB (NOT allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) → msg.sender = router
  - Pool calls extension.beforeSwap(router, ...)
  - Extension checks allowedSwapper[pool][router] → TRUE
  - Swap executes for userB despite userB not being allowlisted

Result:
  - userB trades against LP liquidity
  - Allowlist protection is completely bypassed
  - LP funds exposed to unrestricted flow
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
