### Title
SwapAllowlistExtension Allowlist Bypassed via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router address is allowlisted (the only way to let legitimate users use the router), any non-allowlisted user can bypass the guard by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by the pool from its own `msg.sender` — i.e., whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) is used, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [2](#0-1) 

The pool therefore sees `msg.sender = router` and passes `sender = router` to `_beforeSwap`: [3](#0-2) 

The extension then evaluates `allowedSwapper[pool][router]` — the router's allowlist status — rather than the actual end user's. This creates an irreconcilable dilemma for the pool admin:

- **Router not allowlisted**: Allowlisted users cannot use the router at all; they must call `pool.swap()` directly.
- **Router allowlisted**: Every user on the network can bypass the allowlist by routing through the router, because the router imposes no per-user restrictions of its own.

The `DepositAllowlistExtension` does not share this flaw because it gates by `owner` (the position owner explicitly passed to `pool.addLiquidity`), not by `sender`: [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool that only allows its own hedging bots) can be fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter`. The unauthorized user swaps against the pool's LP liquidity at oracle-derived prices, extracting value from LPs who expected only vetted counterparties. This is a direct loss of LP principal and constitutes broken core pool functionality.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to have a normal UX will allowlist the router. The bypass is then immediately available to every address on the network with no special privileges, no malicious setup, and no non-standard token behavior required. The trigger is a normal `exactInputSingle` call.

---

### Recommendation

The extension must gate the actual end user, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Pass the real payer through `extensionData`**: The router already stores the real payer in transient storage (`_getPayer()`). It can encode the real `msg.sender` into `extensionData` before calling the pool, and the extension can decode and verify it. This requires the extension to trust that the router correctly reports the payer, which is acceptable if the router itself is a trusted periphery contract.

2. **Check `recipient` instead of `sender`**: If the pool's design intent is to gate who *receives* the output, checking `recipient` (the second argument to `beforeSwap`) is router-transparent. However, this changes the semantics of the allowlist.

The cleanest fix is option 1: the router encodes `abi.encode(msg.sender)` into `extensionData`, and the extension decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][userA] = true        // legitimate user
  allowedSwapper[pool][router] = true       // admin allowlists router so userA can use it

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        recipient: userB,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  router calls pool.swap(userB, true, X, ...)
    → pool.msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → extension checks allowedSwapper[pool][router] = true
    → guard passes
    → userB receives token output, LP funds drained

Result: userB swaps successfully despite not being allowlisted.
```

### Citations

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
