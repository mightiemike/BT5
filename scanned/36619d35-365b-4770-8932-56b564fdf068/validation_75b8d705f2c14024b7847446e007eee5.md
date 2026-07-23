The call chain is fully traceable. Here is the finding:

---

### Title
Router-Mediated Swaps Substitute Router Identity for Caller Identity in `SwapAllowlistExtension::beforeSwap`, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool — not the original EOA. The hook therefore checks whether the **router** is allowlisted, not the actual trader. Any pool that allowlists the router (the natural setup for router-mediated swaps) exposes its allowlist to every user of the router.

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool::swap`, the pool calls:

```solidity
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
``` [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that value unchanged as the first argument to every configured extension:

```solidity
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
``` [2](#0-1) 

**Step 2 — The hook checks that `sender` argument, not `tx.origin`.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  ...
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`. [3](#0-2) 

**Step 3 — The router calls `pool.swap()` as itself.**

`exactInputSingle` (and all other `exact*` entry points) call the pool directly with no mechanism to forward the original caller's identity:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // user-controlled but hook ignores it for identity
  );
``` [4](#0-3) 

When this executes, `msg.sender` seen by the pool is the **router address**, so `sender` passed to `beforeSwap` is the router address.

**Step 4 — The identity substitution.**

| Call path | `sender` seen by hook |
|---|---|
| EOA → `pool.swap()` directly | EOA address |
| EOA → `router.exactInputSingle()` → `pool.swap()` | **Router address** |

The hook has no way to distinguish these two cases. It only sees the router.

**Concrete attack (single transaction):**

1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists only addresses A and B.
2. Pool admin also allowlists the router so that A and B can use it conveniently.
3. Unprivileged attacker C calls `router.exactInputSingle(...)` targeting that pool.
4. Router calls `pool.swap(...)` → hook checks `allowedSwapper[pool][router]` → router is allowlisted → **swap succeeds for C**.

The allowlist is completely bypassed. Alternatively, if the admin does *not* allowlist the router, then A and B cannot use the router at all — the extension is incompatible with the router by design.

The multi-hop `exactInput` path compounds this: for hops after the first, the payer is `address(this)` (the router itself), so the router's identity is presented to every intermediate pool's hook as well. [5](#0-4) 

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for pools to restrict trading to a defined set of counterparties (e.g., compliance whitelists, institutional-only pools, risk-controlled venues). Router-mediated swaps completely nullify this control: any user who routes through `MetricOmmSimpleRouter` is checked as the router, not as themselves. A pool that allowlists the router to support normal UX is fully open to all users. This is a broken core access-control boundary — an unprivileged path bypasses a live loss-prevention / access control.

### Likelihood Explanation

The router is the standard entry point for end users. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps must allowlist the router, which immediately opens the bypass. The attack requires no special privileges, no timing, and no prior state manipulation — a single transaction suffices.

### Recommendation

The hook must verify the **original initiator**, not the immediate caller. Options:

1. **Pass original sender via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the hook decodes and verifies it, but only when `msg.sender` (the pool's caller) is a trusted router. This requires a trusted-router registry.
2. **Use `tx.origin` as a fallback**: Only acceptable if the hook is designed for EOA-only allowlists and the threat model excludes contract callers.
3. **Require direct pool interaction**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and the router must revert when targeting such pools. This is operationally fragile.
4. **Preferred**: Redesign the hook to accept an authenticated `originalSender` field that the router signs or passes through a trusted channel, similar to how Uniswap v4 hooks receive `hookData`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: setAllowedToSwap(pool, routerAddress, true)
    (necessary for A and B to use the router)
  - Pool admin: setAllowedToSwap(pool, attackerEOA, false)
    (attacker is explicitly NOT allowlisted)

Attack (1 transaction):
  - attacker calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...)  [msg.sender = router]
  - pool calls extension.beforeSwap(sender=router, ...)
  - hook checks allowedSwapper[pool][router] → true
  - swap executes successfully for attacker

Result: attacker bypasses the allowlist entirely.
```

The "two transactions" framing in the question is not required — the bypass is a single-transaction structural flaw, not a timing race. The invariant "the hook checks the exact swapper identity" is broken whenever the router is the intermediary.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
