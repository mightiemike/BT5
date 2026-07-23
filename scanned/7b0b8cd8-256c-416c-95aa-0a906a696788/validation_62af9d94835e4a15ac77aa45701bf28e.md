### Title
SwapAllowlistExtension gates the router address instead of the end-user, allowing any caller to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address — not the end user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the per-user allowlist by routing through the same public router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` (the router) as `sender` to the extension.**

In `MetricOmmPool.swap()`:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards that same address as `sender` to every configured extension.**

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)  // sender = router address
)
``` [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the router.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself the pool's `msg.sender`.**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The pool admin faces an impossible choice:

| Admin decision | Effect |
|---|---|
| Do **not** allowlist the router | Legitimate users who need the router are blocked |
| **Allowlist the router** | Every unprivileged address can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously allows router-mediated swaps for some users while blocking others, because the identity checked is always the router's address, never the end user's.

---

### Impact Explanation

A curated pool's swap allowlist is completely defeated for any user who routes through `MetricOmmSimpleRouter`. The attacker does not need any special privilege — the router is a public, permissionless contract. If the pool admin has allowlisted the router (the only way to let legitimate users use it), every address on-chain can trade on the restricted pool. This breaks the core access-control invariant of `SwapAllowlistExtension` and constitutes a direct policy bypass with fund-impacting consequences: unauthorized parties can drain liquidity through arbitrage or directional trading on pools that were designed to be restricted.

---

### Likelihood Explanation

The trigger is unprivileged and reachable by any address. The only precondition is that the pool admin has allowlisted the router — which is the expected operational state for any pool that intends to support router-mediated swaps. The `MetricOmmSimpleRouter` is the canonical public swap entrypoint documented in the periphery layer, so this precondition is routinely satisfied in production.

---

### Recommendation

The allowlist check must gate the **economic actor** (the end user), not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: pass the original `msg.sender` (the end user) as the `recipient` or encode it in `extensionData` so the extension can recover it. Alternatively, the router can pass the user address as the `recipient` and the extension can check `recipient` instead of `sender` for allowlist purposes.

2. **In `SwapAllowlistExtension.beforeSwap`**: check `recipient` (the address that receives output tokens and is economically attributed to the trade) rather than `sender` (the immediate caller of `pool.swap()`), or require that `sender` and `recipient` match for allowlisted pools, preventing router-mediated separation.

The deposit allowlist already demonstrates the correct pattern: `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner, the economically relevant actor) and ignores the `sender` parameter entirely. [5](#0-4) 

---

### Proof of Concept

**Setup**: Pool P is configured with `SwapAllowlistExtension`. Pool admin allowlists only Alice (`allowedSwapper[P][Alice] = true`) and also allowlists the router (`allowedSwapper[P][router] = true`) so Alice can use the router.

**Attack**:
1. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({ pool: P, recipient: Bob, ... })`.
2. The router calls `P.swap(Bob, ...)` — pool's `msg.sender` = router.
3. Pool calls `_beforeSwap(router, Bob, ...)`.
4. Extension receives `sender = router`, checks `allowedSwapper[P][router]` → `true`.
5. Swap executes. Bob trades on the restricted pool without being allowlisted.

**Expected**: revert `NotAllowedToSwap`.
**Actual**: swap succeeds because the router's address passes the allowlist check.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
