### Title
SwapAllowlistExtension.beforeSwap gates the router address instead of the end user, allowing allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When any user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract, not the end user. A pool admin who allowlists the router address (a natural step to let their approved users access the router) inadvertently grants every user on-chain the ability to bypass the swap allowlist entirely.

---

### Finding Description

**Root cause — identity mismatch in the hook argument:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every extension:

```solidity
// MetricOmmPool.sol line 231
_beforeSwap(
    msg.sender,   // ← pool's msg.sender, not the end user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then uses that value as the identity to check:

```solidity
// SwapAllowlistExtension.sol lines 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` = pool (correct key), `sender` = pool's `msg.sender` = **router** when the call originates from `MetricOmmSimpleRouter`.

**Router call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the end user
```

**Contrast with DepositAllowlistExtension (correct):**

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The deposit extension correctly gates `owner` (the economically relevant actor). The swap extension gates `sender` (the immediate caller), which collapses to the router for all router-mediated swaps.

---

### Impact Explanation

A pool admin who wants to allow their approved users to use the router will add the router address to `allowedSwapper[pool]`. Once the router is allowlisted, **every user on-chain** can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` against that pool and the `beforeSwap` guard passes unconditionally. The swap allowlist — the sole mechanism for restricting who may trade in the pool — is fully neutralized. Any unauthorized actor can execute swaps, drain liquidity at oracle-derived prices, or front-run LP positions in a pool that was intended to be access-controlled.

---

### Likelihood Explanation

Pool admins who deploy a `SwapAllowlistExtension` pool and also want their approved users to benefit from the router's slippage protection and multi-hop routing will naturally allowlist the router. There is no documentation warning against this. The pattern is standard in DeFi (allowlist the trusted router, not every individual user). The bypass is then trivially reachable by any address that calls the router — no special privileges, no flash loans, no multi-block setup.

---

### Recommendation

The extension must gate the actual end user, not the intermediate caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: Require the router to encode `msg.sender` (the end user) into `extensionData` and have the extension decode and check that address. The pool admin configures the router as a trusted forwarder, and the extension verifies the forwarded identity.

2. **Check `tx.origin` as a fallback for router-mediated calls**: When `sender` is a known router, fall back to `tx.origin`. This is acceptable in a non-meta-transaction context and is consistent with how Uniswap v3 periphery handles identity forwarding.

Either way, the `SwapAllowlistExtension` must not treat the router address as the identity to gate.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)          // alice is approved
  pool admin calls setAllowedToSwap(pool, address(router), true) // router added so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: restrictedPool,
        tokenIn: token0,
        ...
    })

  router calls pool.swap(...)  →  msg.sender = router
  pool calls _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  bob's swap executes successfully — allowlist bypassed
```

Bob pays the correct token amount and receives the correct output; the pool does not revert. The allowlist that was supposed to restrict trading to alice (and other approved addresses) is completely ineffective for any user who routes through the router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
