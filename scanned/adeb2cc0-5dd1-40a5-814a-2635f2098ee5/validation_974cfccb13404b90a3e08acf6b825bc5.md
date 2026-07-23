### Title
`SwapAllowlistExtension.beforeSwap()` gates the router address instead of the actual end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When any user swaps through `MetricOmmSimpleRouter`, that value is always the router's own address, never the real end user. A pool admin who allowlists the router to let legitimate users reach the pool through the supported periphery path simultaneously opens the allowlist to every unprivileged caller, defeating the guard entirely.

---

### Finding Description

**Root cause — wrong actor bound in the hook:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)   // sender == msg.sender of pool.swap()
    )
);
```

`SwapAllowlistExtension.beforeSwap()` then gates on that value:

```solidity
// SwapAllowlistExtension.sol  line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is whoever called `pool.swap()`.

**The router always appears as `sender`:**

Every public entry point in `MetricOmmSimpleRouter` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol  line 72-80  (exactInputSingle)
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

Because the router is `msg.sender` of that call, the pool passes `router_address` as `sender` to the extension. The real end user's address is never surfaced to the hook. The `extensionData` bytes are forwarded from the user but the extension ignores them (the last parameter is unused).

**The forced dilemma:**

| Admin configuration | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Router **not** allowlisted | ❌ Reverts | ❌ Reverts |
| Router **allowlisted** | ✅ Passes | ✅ Passes — **bypass** |

A pool admin who wants allowlisted users to reach the pool through the supported periphery path must allowlist the router. Doing so makes `allowedSwapper[pool][router] == true`, so the guard passes for every caller regardless of their individual allowlist status.

The `exactInput` multi-hop path has the same property: for every hop the router calls `pool.swap()`, so every hop's extension check sees `sender = router`.

---

### Impact Explanation

Any unprivileged user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist is the primary access-control mechanism for pools that restrict trading to vetted counterparties. Bypassing it allows unauthorized swaps at oracle-derived prices, which can drain LP-owned assets if the pool was designed to trade only with specific, trusted parties. This is a direct admin-boundary break: the pool admin's configured allowlist is rendered ineffective by a supported public periphery path.

---

### Likelihood Explanation

Medium-high. Any pool operator who deploys a `SwapAllowlistExtension` and also wants users to access the pool through the canonical router will allowlist the router — this is the natural, expected configuration step. The non-obvious consequence (that allowlisting the router opens the gate to everyone) is not documented and is not guarded against in the extension or the router. The trigger requires no special privilege beyond the admin's own reasonable setup action.

---

### Recommendation

The `SwapAllowlistExtension` must check the actual end user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Router-forwarded identity:** Have `MetricOmmSimpleRouter` encode the real `msg.sender` into `extensionData` for each hop, and update `SwapAllowlistExtension.beforeSwap()` to decode and check that address when `sender` is a known router.
2. **Recipient-based check:** Gate on `recipient` instead of (or in addition to) `sender` for router-mediated flows, since the recipient is the economic beneficiary of the swap.

Either way, the invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" must be enforced at the extension level, not assumed from the call path.

---

### Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true).
3. Admin calls setAllowedToSwap(pool, router, true)   ← necessary for alice to use the router.

Attack
──────
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})

5. Router calls pool.swap(recipient, ...).
   pool.swap() sets sender = msg.sender = router_address.

6. SwapAllowlistExtension.beforeSwap(router_address, ...) checks:
       allowedSwapper[pool][router_address]  →  true   ← passes

7. bob's swap executes at oracle price.
   The allowlist is fully bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) 
<cite repo="patrichyt/2026-07-metric-dev-oyakhil-main--014" path="metric-periphery/contracts/MetricOmmSimpleRouter.sol" start="67"

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
