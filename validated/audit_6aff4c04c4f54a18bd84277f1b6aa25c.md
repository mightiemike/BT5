### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument (the direct caller of `pool.swap`) against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their approved users inadvertently opens the pool to **every user** who calls the router, completely defeating the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

The pool passes its own `msg.sender` as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

From the pool's perspective, `msg.sender` is the **router**, not the originating EOA. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | **Every user** can bypass the allowlist via the router |

The second branch is the fund-impacting path: the pool admin, intending to allow their approved users to trade through the standard periphery, must allowlist the router address. Once the router is allowlisted, `allowAllSwappers[pool]` is effectively true for any caller who routes through it.

---

### Impact Explanation

Any unprivileged user can bypass a `SwapAllowlistExtension`-gated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`). The allowlist is supposed to restrict who may trade — for compliance, risk management, or exclusive-access pools. With the router allowlisted, the restriction is completely nullified. Users who should be blocked can execute swaps, draining pool liquidity at oracle-anchored prices that were only intended for approved counterparties.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract. Any user can call it.
- A pool admin who wants router-mediated swaps for their allowlisted users **must** allowlist the router — there is no other mechanism.
- The bypass requires no special privileges, no malicious setup, and no non-standard tokens. Any EOA can trigger it.

---

### Recommendation

The extension must check the **originating user**, not the direct pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is fragile.

2. **Preferred — check `tx.origin` or use a forwarded-sender pattern**: The pool or router should forward the true initiator as a dedicated field. The cleanest fix is to have the pool pass a separate `originator` (e.g., `tx.origin` when `msg.sender` is a known router, or a dedicated field in the swap call) and have the extension check that field.

3. **Simplest production fix**: The `beforeSwap` hook should check `sender` only when `sender` is not a known router, and require the router to attest the real user via `extensionData`. Alternatively, the allowlist should gate on `tx.origin` for EOA-only pools (acceptable when non-contract callers are the intended audience).

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is approved
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be approved for alice to use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(...) → msg.sender = router
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] → true
  - bob's swap executes successfully despite not being on the allowlist

Result:
  - bob trades against a pool restricted to approved counterparties
  - The allowlist invariant is broken; any user with router access can trade
``` [4](#0-3) [5](#0-4) [2](#0-1)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
