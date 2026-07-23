### Title
`SwapAllowlistExtension` checks the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` inside `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` in the pool is the **router**, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the actual trader is allowlisted. This is the direct analog of the external report's unconditional state-change bug: a guard that should be conditional on the real actor is unconditionally applied to the wrong actor.

---

### Finding Description

`MetricOmmPool.swap()` always passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` — so `msg.sender` inside the pool is the **router address**. The extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`, completely ignoring the actual end user.

This creates two mutually exclusive failure modes:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user — including non-allowlisted ones — can bypass the gate by routing through the router |
| Router **is not** allowlisted | Allowlisted users cannot use the router at all; their swaps revert |

The pool admin cannot simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same. [4](#0-3) 

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only) that deploys `SwapAllowlistExtension` and allowlists the router to support normal UX inadvertently opens the pool to **any** unprivileged user. Those users can execute swaps against LP positions that were never intended to face them, extracting value from LPs who deposited under the assumption of a restricted counterparty set. This is a direct loss of LP principal through unauthorized swap execution — matching the "allowlist bypass" and "wrong-actor binding" impact categories. [5](#0-4) 

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Pool admins who want their allowlisted users to have a normal UX will allowlist the router. The bypass is therefore reachable by any unprivileged user on every curated pool that supports router-mediated swaps. No special privileges, flash loans, or unusual token behavior are required — a single `exactInputSingle` call suffices. [6](#0-5) 

---

### Recommendation

The extension must gate the **economic actor** — the address that initiated the trade and will receive or pay tokens — not the intermediary contract. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the real user address in `extensionData`; the extension decodes and checks it. This requires a trusted router assumption.
2. **Check `tx.origin` as a fallback** (not recommended for general use, but acceptable in a controlled periphery context).
3. **Preferred**: Redesign the hook interface so the pool passes both `msg.sender` (the immediate caller) and an explicit `originator` field that the router populates, allowing the extension to check the correct identity.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to let allowlisted users use the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       tokenIn: token0,
       tokenOut: token1,
       ...
     })
  2. Router calls pool.swap(recipient, zeroForOne, ..., extensionData).
     Inside pool.swap(): msg.sender = router.
  3. Pool calls extension.beforeSwap(sender=router, ...).
  4. Extension checks: allowedSwapper[pool][router] == true → passes.
  5. Swap executes. attacker receives token1 from the curated pool
     despite never being individually allowlisted.

Result:
  attacker successfully trades on a pool that was supposed to be
  restricted to allowlisted counterparties only.
``` [3](#0-2) [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
