### Title
`SwapAllowlistExtension` gates the router address instead of the real user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` from the pool's perspective — the router contract — not the end user. Any user who routes through `MetricOmmSimpleRouter` is checked against the allowlist as the router address. If the router is allowlisted (the natural operational choice), the allowlist is completely bypassed for every user.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to every extension hook:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

So `msg.sender` inside the pool is the **router address**, not the end user. The pool passes the router address as `sender` to `SwapAllowlistExtension.beforeSwap`:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the end user). The check becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

This is the direct analog of the external report: in that report `msg.sender` was the relayer instead of the signing user; here `sender` is the router instead of the actual swapper.

---

### Impact Explanation

**Critical/High — allowlist completely bypassed, breaking a core access-control invariant.**

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties). To allow those users to swap conveniently, the admin allowlists the public `MetricOmmSimpleRouter`. Because the extension checks the router's address rather than the user's address, every call through the router passes the allowlist check regardless of who the end user is. Any non-allowlisted address can swap by simply calling the router instead of the pool directly, draining or trading against the pool's liquidity without authorization.

The inverse also holds: if the admin does not allowlist the router, allowlisted users cannot use the router at all, breaking the intended UX and forcing direct pool interaction.

---

### Likelihood Explanation

**High.** The `MetricOmmSimpleRouter` is the primary public entry point for swaps. Pool admins who want to use the allowlist extension will almost certainly allowlist the router to preserve usability, triggering the bypass for all users. No special permissions, flash loans, or unusual token behavior are required — any EOA can call `exactInputSingle` on the router.

---

### Recommendation

The extension must check the identity of the actual end user, not the intermediary. Two complementary fixes:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and verifies it. This requires a coordinated convention between router and extension.

2. **Check `sender` only for direct pool calls; reject router-mediated calls unless the router forwards the real user**: The cleanest on-chain fix is for the pool to accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` before the pool call, and the pool forwards that value as `sender` to extensions. This mirrors the ERC-2771 `_msgSender()` pattern from the external report.

Until fixed, the `SwapAllowlistExtension` should not be used with any pool that is also accessible through the `MetricOmmSimpleRouter`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - allowAllSwappers[pool] = false
  - allowedSwapper[pool][router] = true   ← admin allowlists the router for usability
  - allowedSwapper[pool][attacker] = false ← attacker is NOT allowlisted

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender in pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes — attacker receives output tokens

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; allowlist completely bypassed
```

**Relevant code locations:**

- Pool passes `msg.sender` as `sender` to hooks: [1](#0-0) 
- Router calls `pool.swap()` directly, making itself `msg.sender` in the pool: [2](#0-1) 
- Extension checks `allowedSwapper[pool][sender]` where `sender` is the router: [3](#0-2) 
- `ExtensionCalling._beforeSwap` forwards the pool's `msg.sender` verbatim as `sender`: [4](#0-3)

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
