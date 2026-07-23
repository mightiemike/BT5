### Title
`SwapAllowlistExtension` gates on the router address instead of the actual swapper, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented as gating `swap` **by swapper address, per pool**. However, the `sender` parameter it inspects is `msg.sender` of the pool — the immediate caller — not the end user. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract. If the pool admin allowlists the router (a natural step to enable router-based trading on a curated pool), every user who calls the router can bypass the per-user restriction entirely.

---

### Finding Description

**Pool → Extension call chain**

In `MetricOmmPool.swap`, the `_beforeSwap` hook is dispatched with `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap() — the router, not the end user
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

**Extension check**

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Router path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender` of the pool:

```solidity
// MetricOmmSimpleRouter.sol
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

**Bypass scenario**

A pool admin who wants to allow router-based trading on a curated pool adds the router to the allowlist (`allowedSwapper[pool][router] = true`). From that moment, every user — including those explicitly excluded from the per-user allowlist — can call `router.exactInputSingle` and the extension check passes because it sees the allowlisted router address, not the actual user.

The `DepositAllowlistExtension` does **not** share this flaw: it inspects `owner` (the position owner passed explicitly), which correctly identifies the economic beneficiary regardless of the intermediary.

---

### Impact Explanation

The `SwapAllowlistExtension` is the primary on-chain mechanism for curating who may trade on a pool. When the router is allowlisted, the guard silently fails open for all router users. Any user excluded from the per-user allowlist can trade freely, defeating the curation policy. On pools where the allowlist is used to restrict access to professional counterparties or to limit exposure during sensitive periods, this allows arbitrary users to extract value from LP positions at prices the LPs did not intend to offer to the general public — a direct LP-fund-impacting consequence.

---

### Likelihood Explanation

The bypass requires the pool admin to allowlist the router. This is a natural and expected action: a pool admin who wants to support the standard periphery flow will add the router to the allowlist, not realising that doing so grants every router user the same access as individually allowlisted addresses. The router is a canonical, factory-known contract, so allowlisting it is a low-friction, plausible configuration step.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the **transport layer** (the router). Two viable approaches:

1. **Check `recipient` instead of `sender`** — for most swap flows the recipient is the end user. This is imperfect for multi-hop paths where intermediate recipients are contracts.
2. **Require the actual user address in `extensionData`** — the router encodes the real user in `extensionData`; the extension decodes and verifies it, with the pool enforcing that `extensionData` is caller-supplied and not forgeable.
3. **Allowlist by `recipient`** — change the mapping key from `sender` to `recipient` and document that the allowlist gates on who receives the output, which is the economically meaningful actor.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` wired as `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to enable router-based trading for `userA`.
4. `userB` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(userB, ...)` — pool dispatches `_beforeSwap(msg.sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `userB` successfully trades on a pool from which they were supposed to be excluded. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
