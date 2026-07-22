### Title
Swap Allowlist Bypassed via Router: `sender` Identity Lost When Pool Is Called Through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (required for any router-based swap to succeed on a restricted pool), every user — including those explicitly excluded — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passes as the first argument to `_beforeSwap`. In `MetricOmmPool`, both `swap()` and `simulateSwapAndRevert()` pass `msg.sender` of the pool call as `sender`:

```solidity
// MetricOmmPool.sol (simulateSwapAndRevert, same pattern as swap)
_beforeSwap(
    msg.sender,   // ← this is the router when called via router
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is the router. The actual user's address (`msg.sender` of the router call) is stored only in the router's internal callback context and is never forwarded to the pool or the extension. The extension therefore sees `sender = router`, not the actual user.

**The bypass**: A pool admin who wants to restrict swaps to a specific set of users configures `SwapAllowlistExtension` and allowlists individual addresses. To allow those users to also use the router, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check `!allowedSwapper[msg.sender][sender]` passes for every user who routes through the router — including those explicitly excluded from the allowlist.

This is structurally identical to the H-03 pattern: the guard checks one condition (`allowedSwapper[pool][sender]`) but the second relevant state — the actual originating user — is invisible to the extension because the intermediate contract (router) replaces the user's identity in `msg.sender`.

---

### Impact Explanation

The swap allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated, compliance-restricted, or institutional-only pools). Any non-allowlisted user can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` on a pool where the router is allowlisted. The pool admin cannot simultaneously allow router-based swaps for legitimate users and block non-allowlisted users from using the router — the two goals are mutually exclusive under the current design.

Consequence: unauthorized users execute swaps on a pool that was intended to be restricted, violating the pool's access-control invariant. Depending on the pool's purpose, this can result in regulatory non-compliance, unauthorized extraction of liquidity at oracle prices, or circumvention of risk controls.

---

### Likelihood Explanation

- The router is a public, permissionless contract. Any user can call it.
- For a restricted pool to be usable at all via the router, the admin must allowlist the router. This is the expected operational setup.
- Once the router is allowlisted, the bypass requires zero special privileges: any user calls `exactInputSingle` with the restricted pool address.
- The bypass is reachable in one transaction with no preconditions beyond the router being allowlisted.

---

### Recommendation

The extension must gate the **originating user**, not the immediate caller of the pool. Two approaches:

1. **Pass the original user through the pool**: Add an optional `originator` field to the pool's `swap()` signature (or encode it in `extensionData`), and have the router populate it with `msg.sender`. The extension reads the originator from `extensionData` when present, falling back to `sender` for direct pool calls.

2. **Check `sender` against the router's stored originator**: The router already stores the originating user in its callback context (`_setNextCallbackContext(..., msg.sender, ...)`). Expose this via a view function and have the extension query it. This couples the extension to the router, which is undesirable.

The cleanest fix is approach 1: the pool passes `msg.sender` as `sender` for direct calls, and the router encodes the real user in `extensionData` so the extension can recover the true identity.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists `alice` (a legitimate user) and the router address.
3. Pool admin does **not** allowlist `bob` (an unauthorized user).
4. `bob` calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool.
5. The pool calls `_beforeSwap(msg.sender=router, ...)`.
6. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps on a pool he was explicitly excluded from.

The allowlist check that should have blocked `bob` at step 6 instead passes because the router's address — not `bob`'s — is the `sender` the extension sees. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L321-331)
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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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
