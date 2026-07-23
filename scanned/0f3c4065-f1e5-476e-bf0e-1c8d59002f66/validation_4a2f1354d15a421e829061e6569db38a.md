### Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address — not the actual user's address. If the router is allowlisted (the only way to enable router-based swaps), every user on-chain can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` argument: [1](#0-0) 

`sender` is the first parameter the pool passes to every extension hook. In `MetricOmmPool.swap`, that value is always `msg.sender` — the direct caller of the pool: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap()` directly: [3](#0-2) 

`msg.sender` to the pool is therefore the **router contract address**, not the actual user. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma for any pool admin who deploys `SwapAllowlistExtension`:

| Router allowlisted? | Effect |
|---|---|
| **No** | Allowlisted users cannot swap through the router at all — core swap functionality is broken for the intended audience |
| **Yes** | Every address on-chain can bypass the per-user allowlist by routing through the router |

The contrast with `DepositAllowlistExtension` makes the design intent clear: the deposit guard correctly checks `owner` (the LP position owner — the economically relevant actor), not `sender`: [4](#0-3) 

`SwapAllowlistExtension` should apply the same principle but does not.

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers) achieves no protection once the router is allowlisted. Any unprivileged address can call `exactInputSingle` or `exactInput` on the router and trade freely in the supposedly gated pool. This is a direct allowlist bypass reachable by any user with no special privileges, no admin cooperation, and no non-standard tokens.

---

### Likelihood Explanation

The router is the standard, documented user-facing entry point for swaps. Pool admins who deploy `SwapAllowlistExtension` will inevitably need to allowlist the router to make the pool usable for normal users, at which point the bypass is universally available. The trigger requires only a standard router call — no flash loans, no callbacks, no special timing.

---

### Recommendation

The extension must check the actual user identity, not the intermediary. Two sound approaches:

1. **Decode user from `extensionData`**: Require callers (router or direct) to encode the actual user address in `extensionData`; the extension decodes and verifies it. The router would need to forward the real `msg.sender` in the extension payload.
2. **Mirror the deposit pattern**: Pass the real payer/user through a dedicated field in the hook arguments, analogous to how `owner` is passed separately from `sender` in `beforeAddLiquidity`, and gate on that field instead of `sender`.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists only `trustedUser` and the router (to enable router swaps).
// allowedSwapper[pool][trustedUser] = true
// allowedSwapper[pool][router]      = true   ← required for router to work

// Attacker (not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         token0,
    recipient:       attacker,
    zeroForOne:      true,
    amountIn:        1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:   type(uint128).max,
    deadline:        block.timestamp,
    extensionData:   ""
}));
// Pool.swap() is called with msg.sender = router.
// Extension checks allowedSwapper[pool][router] == true → passes.
// Attacker swaps successfully despite not being on the allowlist.
```

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
