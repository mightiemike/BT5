### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass a Curated Pool's Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. The pool always sets that argument to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the real user. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), every user — including non-allowlisted ones — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Step 1 — Pool forwards its own `msg.sender` as `sender`.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`_beforeSwap` encodes that value as the `sender` field and dispatches it to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks that forwarded `sender`.**

The extension's guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whatever the pool forwarded — the router's address when the call came through the router. [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, making itself the pool's `msg.sender`.**

`exactInputSingle` (and every other router entry point) calls the pool without any mechanism to inject the real user's address into the extension path: [4](#0-3) 

The real user's address is stored in transient storage only for the payment callback (`_setNextCallbackContext`), never surfaced to the extension.

**Step 4 — The bypass.**

A pool admin who wants allowlisted users to trade through the router must add the router to the allowlist:

```
allowedSwapper[pool][router] = true
```

Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including addresses the admin never intended to allow. There is no way to simultaneously (a) permit allowlisted users to use the router and (b) block non-allowlisted users from using the router, because the extension never sees the real user's address.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool's LP positions, extracting value at oracle-derived prices that the pool's LPs did not consent to trade at with those counterparties. This is a direct loss of LP principal and a complete failure of the pool's core access-control invariant.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who enables router access for their allowlisted users automatically opens the bypass. The attacker needs no special privileges — a single call to `exactInputSingle` with the target pool is sufficient. The condition (router allowlisted on a curated pool) is the normal operational state for any curated pool that supports router-mediated trading.

---

### Recommendation

The extension must gate on the **real initiating user**, not the intermediary. Two complementary fixes:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; `SwapAllowlistExtension` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct calls.

2. **Check `sender` only for direct calls; reject router calls on curated pools**: Add a flag to `SwapAllowlistExtension` that rejects any `sender` that is a known router unless the real user is also encoded and verified.

The simplest correct fix is option 1: the router always appends `abi.encode(msg.sender)` to `extensionData`, and the extension decodes it as the authoritative identity to check against the allowlist.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the intended user
  allowedSwapper[pool][router] = true   // admin adds router so alice can use it

Attack:
  charlie (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., extensionData: ""})

  Execution:
    router → pool.swap(...)          // pool.msg.sender = router
    pool   → _beforeSwap(router, …)
    ext    → allowedSwapper[pool][router] == true  ✓  // charlie's swap passes
```

Charlie's swap executes against the pool's LP positions despite never being allowlisted, because the extension sees `router` (allowlisted) instead of `charlie` (not allowlisted).

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
