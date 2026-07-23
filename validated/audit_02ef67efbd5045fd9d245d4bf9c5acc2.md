### Title
`SwapAllowlistExtension` checks the router's address instead of the actual user when swaps are routed through `MetricOmmSimpleRouter`, enabling a complete allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address rather than the actual user. A pool admin who allowlists the router address to enable router-mediated swaps for their curated users inadvertently opens the pool to **all** users, completely bypassing the intended allowlist restriction.

---

### Finding Description

The `SwapAllowlistExtension` is designed to restrict swap access on a per-pool basis. Its `beforeSwap` hook checks:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the pool's `swap()` call:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← sender = immediate caller of pool.swap()
    recipient,
    ...
);
```

**Direct call path**: User → `pool.swap()` → `sender = user` → allowlist checks `allowedSwapper[pool][user]` ✓

**Router call path**: User → `router.exactInputSingle()` → `pool.swap()` → `sender = router` → allowlist checks `allowedSwapper[pool][router]`

If the pool admin allowlists the router address (a natural step to enable router-mediated swaps for their curated users), the check becomes `allowedSwapper[pool][router] == true`, which passes for **any** user who routes through the public `MetricOmmSimpleRouter`.

This is structurally identical to the external bug's class: the `DepositAllowlistExtension` correctly checks `owner` (the economically relevant actor, regardless of who calls `addLiquidity`), while `SwapAllowlistExtension` checks `sender` (the immediate caller), which diverges from the actual user identity when the router intermediates the call. The two allowlist guards apply inconsistent identity fields to analogous gating decisions. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

A curated pool's swap allowlist is completely bypassed. Any user — including those explicitly excluded from `allowedSwapper` — can execute swaps on the pool by routing through the public `MetricOmmSimpleRouter`. This allows unauthorized users to drain pool liquidity at oracle-quoted prices, violating the pool's access-control invariant and causing direct loss of LP principal. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. The bypass requires only that the pool admin has allowlisted the router address — a natural and expected administrative action for any pool that intends to support router-mediated swaps for its curated users. No privileged access, no special tokens, and no malicious setup is required. [6](#0-5) [7](#0-6) 

---

### Recommendation

The `SwapAllowlistExtension` should gate the **actual user** rather than the immediate caller of `pool.swap()`. Two approaches:

1. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is the user, but for multi-hop swaps the intermediate recipient is the router itself, so this is also unreliable.

2. **Require the actual user identity in `extensionData`**: The router forwards `extensionData` unchanged to the pool. The extension can require the caller to embed their identity in `extensionData` and verify it against a signature or a separate allowlist keyed on the embedded address.

3. **Align with `DepositAllowlistExtension`**: The deposit guard correctly checks `owner` (the economically relevant actor). The swap guard should similarly check an actor that cannot be substituted by an intermediary — for example, by requiring the router to embed `msg.sender` in `extensionData` and having the extension verify it. [2](#0-1) [1](#0-0) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     — intending to allow allowlisted users to swap via the router.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. Attacker (not individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
  5. Router calls pool.swap(recipient=attacker, ...) with msg.sender=router.
  6. Pool calls _beforeSwap(sender=router, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  8. Attacker's swap executes against pool liquidity.

Result:
  Attacker bypasses the swap allowlist and executes swaps on a curated pool,
  draining LP funds at oracle-quoted prices.
``` [4](#0-3) [7](#0-6) [5](#0-4)

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
