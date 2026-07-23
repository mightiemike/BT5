### Title
`SwapAllowlistExtension` gates on router address instead of actual end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the pool's `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual end-user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for any user), every unprivileged address can bypass the allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is whatever the pool received as its own `msg.sender`.

The pool passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender, i.e. the router
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

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
```

The router does not forward the original caller's identity. The pool's `msg.sender` is the router contract address. Therefore, `SwapAllowlistExtension` checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

The `DepositAllowlistExtension` avoids this problem because `addLiquidity` carries a separate `owner` parameter that the extension checks — the actual position owner is always forwarded regardless of who the `msg.sender` is. No equivalent "actual user" parameter exists on the swap path.

---

### Impact Explanation

**Allowlist bypass (fund-impacting):** A pool admin who wants to restrict swaps to a specific set of counterparties deploys `SwapAllowlistExtension` and allowlists individual addresses. To also support router-mediated swaps for those users, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and the check passes for every caller regardless of their identity. Any unprivileged address can call `router.exactInputSingle` and swap against the restricted pool, violating the access control invariant the pool admin intended to enforce.

The pool may hold concentrated LP positions intended only for trusted counterparties. Unauthorized swaps drain those positions at oracle-derived prices, causing direct loss of LP principal.

---

### Likelihood Explanation

The scenario is reachable by any unprivileged user whenever:
1. A pool is deployed with `SwapAllowlistExtension` configured on the `beforeSwap` hook order.
2. The pool admin allowlists the router (a natural step to enable router-mediated swaps for legitimate users).

No special privileges, malicious setup, or non-standard tokens are required. The router is a public, permissionless contract.

---

### Recommendation

The `beforeSwap` hook should gate on the actual end-user identity. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is opt-in per call.

2. **Structural fix — add a `realSender` field to the swap interface**: The pool interface could carry both the immediate `msg.sender` and an optional `realSender` (set by trusted routers via transient storage or a dedicated field), and the extension checks `realSender` when present.

Until fixed, pool admins should not allowlist the router if they intend to restrict swaps to specific users; they must require direct pool calls only.

---

### Proof of Concept

**Setup:**
- Pool deployed with `SwapAllowlistExtension` on `beforeSwap`.
- Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is intended to swap.
- Admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router.

**Attack:**
1. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, tokenIn: ..., ...})`.
2. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
3. Pool calls `_beforeSwap(sender=router, ...)`.
4. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
5. Bob's swap executes against the restricted pool, draining LP assets at oracle price.

**Contrast with deposit path:**
- Bob calls `liquidityAdder.addLiquidityExactShares(pool, owner=bob, ...)`.
- Pool calls `_beforeAddLiquidity(sender=liquidityAdder, owner=bob, ...)`.
- `DepositAllowlistExtension` checks `allowedDepositor[pool][owner]` = `allowedDepositor[pool][bob]` → `false` → reverts correctly.

The swap path has no equivalent `owner` parameter, making the identity mismatch structural. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
