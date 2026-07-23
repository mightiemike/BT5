### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of its own `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. The extension therefore checks the router's address against the allowlist, not the economic actor's address. This creates an irresolvable misalignment: the pool admin cannot simultaneously allow specific users to trade via the router and block others, because the router address is the only identity the extension ever sees for router-mediated swaps.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap`** (lines 31–41):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument the pool passes, which is set in `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) is used, it calls `pool.swap(...)` directly, making `msg.sender` to the pool equal to the **router address**:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

The pool admin faces an impossible configuration choice:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router at all (broken UX) |
| Yes | **Any** user can bypass the per-user allowlist by routing through the router |

There is no configuration that allows specific users to trade via the router while blocking others, because the router's address is the only identity the extension sees.

---

### Impact Explanation

A non-allowlisted user can bypass a pool's swap allowlist by calling any of the router's public entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). If the pool admin has allowlisted the router (the only way to let allowlisted users trade via the router), the allowlist is completely ineffective for router-mediated swaps. Any user can trade in a pool that was configured to be restricted to specific counterparties. This breaks the core curation invariant of the extension and exposes LP funds to trades from actors the pool admin explicitly intended to exclude.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. Any user who discovers the pool has a swap allowlist can trivially route through the router. No special privileges, flash loans, or multi-step setup are required — a single public call to the router suffices. The bypass is reliable and repeatable.

---

### Recommendation

The extension must check the **economic actor** (the user who initiated the trade), not the intermediate contract. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Check `sender` (the first argument) as the payer identity and require the router to forward the real user**: The pool could expose a separate `swapOnBehalfOf(address realUser, ...)` entry point that passes `realUser` as `sender` to extensions, while the router uses that entry point.

The simplest correct fix is to have the router pass the originating user's address in `extensionData` and have the extension decode it when present, falling back to `sender` for direct pool calls.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice may swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Charlie (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` to pool = router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → **true**.
8. Charlie's swap executes successfully, bypassing the allowlist entirely.

---

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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
