### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, allowing any user to bypass the per-user allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict which addresses may swap in a pool. Its `beforeSwap` hook gates on the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the user. If the pool admin allowlists the router (which is required for any allowlisted user to trade through the router), every non-allowlisted user can bypass the guard by routing through the same public router contract.

---

### Finding Description

**`SwapAllowlistExtension.beforeSwap` — wrong actor binding**

```solidity
// SwapAllowlistExtension.sol L31-41
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

`msg.sender` here is the pool (correct — enforced by `onlyPool` in `BaseMetricExtension`). `sender` is the argument forwarded by the pool, which is set to `msg.sender` of the `pool.swap()` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the hook
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

Here `msg.sender` of `pool.swap()` is the **router**, so `sender = router` in the hook. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every path the router is the direct caller of `pool.swap()`.

**The invariant break**: A pool admin who wants to allow Alice (but not Bob) to swap through the router must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router]` is `true`, and Bob can call `router.exactInputSingle(...)` targeting the same pool. The hook sees `sender = router` → allowed → Bob's swap executes despite not being on the allowlist.

The `DepositAllowlistExtension` does **not** share this flaw: it gates on `owner` (the position owner), which is passed explicitly and is not overwritten by the adder's address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool with a specific LP strategy) can be freely traded against by any public user via the router. This breaks the core pool functionality the extension was deployed to enforce and can cause direct loss of LP principal if the pool's pricing or liquidity strategy depends on only trusted counterparties executing swaps.

---

### Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` targeting any pool. The only precondition is that the pool admin has allowlisted the router (which is necessary for any legitimate allowlisted user to trade through the router). No privileged access, no special token, no malicious setup is required.

---

### Recommendation

The `beforeSwap` hook should gate on the **economic actor** — the address that initiated the trade — not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

2. **Check `recipient` instead of `sender`** (if the pool's design equates recipient with the economic actor): Not always correct for multi-hop paths where intermediate recipients are the router itself.

3. **Preferred — gate on `sender` but require direct pool calls for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router; users must call `pool.swap()` directly. Enforce this by adding a check that `sender` is never a known router address, or by having the extension reject calls where `sender` is a contract.

The cleanest fix is to have the router forward the originating `msg.sender` as a verified field in `extensionData` and have the extension verify the router's signature or identity before trusting that field.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, Alice, true)` — only Alice should be able to swap.
3. Alice wants to use the router, so the admin also calls `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ..., recipient: Bob, ...})`.
5. Router calls `pool.swap(Bob, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, Bob, ...)`.
7. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true` → hook passes.
8. Bob's swap executes successfully despite not being on the allowlist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
