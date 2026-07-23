### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. If the pool admin allowlists the router (required for router-based swaps to function), every user — including non-allowlisted ones — can bypass the per-user allowlist by routing through the router.

### Finding Description

**Call chain — direct swap (guard works):**
```
User → pool.swap()
  pool: _beforeSwap(msg.sender=User, ...)
  extension.beforeSwap(sender=User, ...)
  check: allowedSwapper[pool][User]  ← correct
```

**Call chain — router swap (guard broken):**
```
User → router.exactInputSingle()
  router → pool.swap()
    pool: _beforeSwap(msg.sender=Router, ...)
    extension.beforeSwap(sender=Router, ...)
    check: allowedSwapper[pool][Router]  ← wrong identity
```

The pool always passes its own `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

The extension checks that `sender` argument against the allowlist, keyed by `msg.sender` (the pool): [2](#0-1) 

The router calls `pool.swap()` directly, making itself the pool's `msg.sender`: [3](#0-2) 

For router-based swaps to work at all, the pool admin must add the router to `allowedSwapper[pool][router] = true`. Once the router is allowlisted, the check `allowedSwapper[pool][Router]` passes for **every** user who routes through it, regardless of whether that user is individually allowlisted.

The pool admin faces an irresolvable dilemma:
- **Allowlist the router** → all users bypass the per-user gate.
- **Don't allowlist the router** → allowlisted users cannot use the standard periphery.

The `DepositAllowlistExtension` does not share this flaw because it gates by `owner` (the LP position owner, an explicit parameter), not by `sender` (the immediate caller): [4](#0-3) 

### Impact Explanation

Any user can swap on a pool protected by `SwapAllowlistExtension` by calling `router.exactInputSingle()` or any other router entry point. The allowlist — intended to restrict which addresses may trade — is completely ineffective for router-mediated swaps. Pools deployed for KYC-gated, compliance-restricted, or institutional-only access are fully open to arbitrary swappers, allowing unauthorized extraction of LP assets at oracle-derived prices.

### Likelihood Explanation

The router is the standard user-facing entry point for the protocol. Any pool admin who enables `SwapAllowlistExtension` and also wants users to access the pool via the router must allowlist the router, triggering the bypass. The attacker needs no special privileges — only knowledge of the router address and the pool address.

### Recommendation

The extension must identify the **economic actor** (the end user), not the immediate caller. Two sound approaches:

1. **Pass `tx.origin` as an additional argument** — acceptable only if the protocol explicitly scopes to EOA-only contexts and documents the limitation.
2. **Require the router to forward the real user identity** — add a `payer`/`originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and check that field. The pool's `beforeSwap` hook already forwards `extensionData` unchanged, so no core changes are needed.

The simplest production fix is option 2: the router encodes `msg.sender` into `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true      // alice is the only allowed swapper
  allowedSwapper[pool][router] = true     // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

  router calls pool.swap(recipient=bob, ...)
    pool calls _beforeSwap(sender=router, ...)
    extension checks allowedSwapper[pool][router] → true  ← passes
  swap executes; bob receives output tokens

Result:
  bob swapped successfully despite not being in the allowlist.
  The allowlist provided zero protection for router-mediated swaps.
```

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
