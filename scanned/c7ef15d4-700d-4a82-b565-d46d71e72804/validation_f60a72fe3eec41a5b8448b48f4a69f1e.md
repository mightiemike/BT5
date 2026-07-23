### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user swaps through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks the **router's address** against the allowlist instead of the actual end-user's address. A pool admin who intends to restrict swaps to specific users cannot enforce that restriction for router-mediated swaps: either the router is not allowlisted (blocking all allowlisted users from using the router) or the router is allowlisted (allowing every user to bypass the gate by routing through it).

---

### Finding Description

**Root cause chain:**

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the `sender` field forwarded to every configured extension: [2](#0-1) 

**Step 2 — The router is `msg.sender` to the pool.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The router is therefore `msg.sender` inside the pool, so `sender` delivered to every extension is the **router address**, not the end-user: [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Step 3 — The allowlist checks the wrong actor.**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is the router (wrong): [4](#0-3) 

The allowlist mapping is keyed `pool → swapper → bool`: [5](#0-4) 

When a user routes through the router, the lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. No guard in `BaseMetricExtension` corrects this; `onlyPool` only validates that the caller is a registered pool, not that `sender` is the actual end-user: [6](#0-5) 

---

### Impact Explanation

Two mutually exclusive failure modes, both fund-impacting:

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | Every allowlisted user is blocked from using the router; the primary swap entry point is broken for all curated-pool users. |
| Router **allowlisted** | Every non-allowlisted user can bypass the curation gate by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router. The allowlist provides zero protection. |

In the bypass scenario, a pool designed to restrict swaps to KYC'd counterparties, institutional LPs, or protocol-controlled addresses is fully open to any public caller. This is a direct loss of the curation invariant and exposes LP principal to unrestricted arbitrage or adversarial flow that the pool admin explicitly intended to block.

---

### Likelihood Explanation

- The router is the canonical, documented user-facing entry point for swaps.
- No special privilege or setup is required; any public caller can invoke `exactInputSingle`.
- The bypass is deterministic and repeatable in every block.
- A pool admin who reads the `SwapAllowlistExtension` NatDoc ("Gates `swap` by swapper address, per pool") has no reason to suspect that router-mediated swaps are exempt.

---

### Recommendation

Pass the **original initiator** through the call chain rather than the immediate `msg.sender`. Two concrete options:

1. **Preferred — thread the real user through the router.** Have the router encode the actual `msg.sender` in `callbackData` or a dedicated field, and have the pool (or extension) recover it. This requires a protocol-level change to the swap interface.

2. **Simpler — check `tx.origin` as a fallback.** In `SwapAllowlistExtension.beforeSwap`, if `sender` is a known router, fall back to `tx.origin`. This is fragile but avoids interface changes.

3. **Allowlist the router as a pass-through and require the router to enforce per-user checks.** Add a `SwapAllowlistExtension`-aware router that verifies the caller against the extension before forwarding to the pool.

The cleanest fix is option 1: the pool should pass the original initiator (stored in transient storage by the router) as `sender` to extensions, not `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin does NOT allowlist the router.
  - Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).

Expected: revert NotAllowedToSwap (Bob is not allowlisted).
Actual:   revert NotAllowedToSwap — but for the wrong reason: the router is not allowlisted.

Now pool admin allowlists the router to let Alice use it:
  - Pool admin calls setAllowedToSwap(pool, router, true).
  - Bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...) again.

Expected: revert NotAllowedToSwap (Bob is not allowlisted).
Actual:   swap succeeds — allowedSwapper[pool][router] == true, so the check passes for Bob.
```

The allowlist is fully bypassed by any user who routes through `MetricOmmSimpleRouter` once the router is allowlisted to serve legitimate users.

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
