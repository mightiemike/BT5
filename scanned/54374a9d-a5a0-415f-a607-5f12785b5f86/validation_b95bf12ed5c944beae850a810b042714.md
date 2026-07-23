### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router**, not the actual user. If the router is allowlisted for a pool (a necessary configuration for multi-hop support), every user—regardless of allowlist status—can bypass the swap gate by routing through the public router.

---

### Finding Description

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)  // sender = router address
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether this `sender` is allowlisted, keyed by `msg.sender` (the pool):

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router (to support multi-hop or single-hop router-mediated swaps) inadvertently opens the gate to every user on the internet.

Contrast this with `DepositAllowlistExtension`, which correctly checks the explicit `owner` parameter—the economic actor—rather than `sender` (the immediate caller). No analogous fix exists for the swap path because the swap interface has no explicit "economic actor" parameter separate from `msg.sender`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd, institutional, or otherwise vetted addresses is completely bypassed. Any unprivileged user calls `router.exactInputSingle()` or `router.exactInput()` targeting the restricted pool. The extension sees `sender = router`, which is allowlisted, and permits the swap. The unauthorized user receives oracle-priced output tokens while LP funds are drained. This is a direct loss of LP principal on a production pool configuration.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract—any EOA or contract can call it.
- A pool admin who configures `SwapAllowlistExtension` and also wants to support router-mediated swaps (the primary user-facing entry point) must allowlist the router. This is the expected production configuration.
- No special privileges, flash loans, or unusual token behavior are required. A single `exactInputSingle` call suffices.
- The bypass is deterministic and repeatable every block.

---

### Recommendation

The `sender` identity passed to `beforeSwap` must represent the **economic actor** (the user who initiated the swap and will pay for it), not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Router-side**: Have the router pass the original `msg.sender` as an explicit "payer" or "initiator" field inside `extensionData`, and update `SwapAllowlistExtension` to decode and check that field when present.

2. **Extension-side (preferred)**: Redesign `SwapAllowlistExtension` to gate on the address that settles the swap callback (the payer), not the `sender` argument. The payer is stored in the router's transient callback context (`_setNextCallbackContext(..., msg.sender, ...)`) and could be forwarded in `extensionData` by the router before calling `pool.swap()`.

Until fixed, pool admins should be warned that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allow router for multi-hop
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: restrictedPool,
           tokenIn: token0,
           ...
       })
  2. Router calls restrictedPool.swap(...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes
  5. Swap executes; attacker receives output tokens from LP funds

Result: attacker bypasses the allowlist and drains LP funds at oracle price.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
