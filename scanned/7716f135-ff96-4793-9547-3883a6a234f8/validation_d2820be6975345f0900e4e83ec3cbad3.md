### Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the **router's address**, not the actual end-user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for permitted users), every unpermissioned user can bypass the allowlist by calling through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls:

```solidity
_beforeSwap(msg.sender, recipient, ...)
``` [1](#0-0) 

`_beforeSwap` encodes that value as the `sender` argument forwarded to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
``` [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks only `sender`.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [3](#0-2) 

`msg.sender` here is the pool (correct), and `sender` is whoever called `pool.swap()`.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender`.**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

When a user calls `exactInputSingle`, the router becomes `msg.sender` of `pool.swap()`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Step 4 — The bypass.**

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including users who were never individually permitted. Any unpermissioned user calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` and the extension returns `beforeSwap.selector` without reverting.

---

### Impact Explanation

The swap allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated, institutional, or compliance-restricted pools). When the router is allowlisted — the only way to let permitted users trade via the standard periphery — the guard is completely neutralised. Any address can execute swaps against the pool, draining LP liquidity at oracle-derived prices, generating protocol fees from unauthorized volume, and violating the pool's intended access policy. This is a direct loss of the pool's access-control invariant with fund-impacting consequences (unauthorized swaps consume LP assets at live oracle prices).

---

### Likelihood Explanation

The trigger requires only that the pool admin allowlists the router address, which is the natural and expected operational step for any pool that wants to support the standard periphery. The router is a public, permissionless contract. No privileged attacker capability is needed beyond calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The path is reachable by any EOA or contract.

---

### Recommendation

The extension must gate the **original end-user**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension.beforeSwap`**: check `recipient` (the economic beneficiary) or require the pool to forward the original `tx.origin`-equivalent. The cleaner solution is to check both `sender` and, when `sender` is a known router, fall back to a secondary identity signal.

2. **Preferred fix**: `MetricOmmSimpleRouter` should forward the original caller's identity in `extensionData` and `SwapAllowlistExtension` should decode and verify it, or the pool interface should carry a separate `originator` field that the router populates with `msg.sender` before calling the pool.

3. **Immediate mitigation**: document that allowlisting the router address defeats the allowlist and instruct pool admins to allowlist individual users only, requiring them to call `pool.swap()` directly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][router] = true   // admin enables router for permitted users
  - allowedSwapper[pool][alice] = true    // alice is a permitted user
  - allowedSwapper[pool][attacker] = false // attacker is NOT permitted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓  (no revert)
    → swap executes at live oracle price
    → attacker receives output tokens, LP assets consumed

Result: attacker bypasses the allowlist and executes an unauthorized swap.
```

The `sender` checked by the extension is the router address, not `attacker`. Because the router is allowlisted, the guard passes unconditionally for all router callers. [3](#0-2) [5](#0-4) [1](#0-0)

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
