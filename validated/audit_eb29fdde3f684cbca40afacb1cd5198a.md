### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Per-User Swap Allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` — the immediate on-chain caller. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted (the natural configuration for enabling standard UX), every user — including those explicitly not allowlisted — can bypass the per-user swap gate by routing through the router. The `DepositAllowlistExtension` avoids this problem by checking `owner` (the economic beneficiary), but the swap extension checks the wrong identity.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← immediate on-chain caller, not the economic user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is now the router, so `sender = router` reaches the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Substitution**: the actual user's identity is silently replaced by the router's address in the allowlist check — an exact structural analog to the lockup-plan segmentation bug where the original plan ID is retained by a worthless piece, letting the taker fill an order with a token that no longer represents the expected value.

**Contrast with `DepositAllowlistExtension`**: the deposit guard correctly checks `owner` (the economic beneficiary supplied explicitly by the caller), not `sender` (the immediate caller). The swap guard has no equivalent mechanism to recover the real user's identity.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd market makers, whitelisted counterparties) must also allowlist the router if they want those users to access the pool through the standard periphery. Once the router is allowlisted, the gate is effectively open to every address that can call the router — which is every address on-chain. Non-allowlisted users can execute swaps against the restricted pool, extracting value at oracle-anchored prices that the pool admin intended to offer only to approved counterparties. This constitutes a direct bypass of a core access-control invariant with fund-impacting consequences (unauthorized swap execution against pool liquidity).

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router, which is the expected operational step for any pool that wants to support the standard periphery UX. The bypass is then available to any unprivileged address with no additional preconditions. The only mitigating factor is that a pool admin who allowlists only individual EOAs (never the router) is not affected — but such a pool would be unusable through the standard periphery, making this a realistic and common configuration.

---

### Recommendation

Gate the actual economic actor, not the immediate caller. Two approaches:

1. **Mirror `DepositAllowlistExtension`**: add a `recipient` parameter check (the address that receives output tokens) instead of `sender`, since `recipient` is the economic beneficiary of the swap and is passed through the router unchanged.

2. **Preferred — check both**: require that either `sender` or `recipient` is allowlisted, or introduce an explicit `swapperOverride` field in `extensionData` that the router populates with `msg.sender` (the original user), and verify it against the allowlist.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order)
  pool admin calls setAllowedToSwap(pool, router, true)   // router allowlisted for UX
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: attacker})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=attacker, ...)   [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes)
      → swap executes, attacker receives output tokens

Result:
  attacker bypasses the per-user allowlist and swaps against the restricted pool.
```

The `DepositAllowlistExtension` is immune to the analogous attack because it checks `owner` (line 38), which the `MetricOmmPoolLiquidityAdder` sets to the actual user, not the adder contract. The `SwapAllowlistExtension` has no equivalent identity-recovery mechanism. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
