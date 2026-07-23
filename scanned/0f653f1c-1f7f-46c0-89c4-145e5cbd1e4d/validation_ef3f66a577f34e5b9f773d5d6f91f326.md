### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of its own `swap()` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` at the pool level, so the extension gates the router's address rather than the actual user's address. If the pool admin allowlists the router (the natural step to let allowlisted users use the standard periphery), every user — including those explicitly excluded — can bypass the restriction by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (enforced by `onlyPool`):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
```

**What `sender` actually is:**

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards it verbatim to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

**What the router passes:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
  priceLimitX64,
  "",
  params.extensionData
);
```

Because the router is the direct caller of `pool.swap()`, `msg.sender` at the pool level is the **router address**. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**The two failure modes:**

| Router allowlist state | Effect |
|---|---|
| Router is allowlisted (admin enables standard periphery) | Every user, including explicitly excluded ones, bypasses the allowlist by routing through the router |
| Router is not allowlisted | Allowlisted users cannot use the router at all — broken core swap functionality |

---

### Impact Explanation

**Direct loss / broken core functionality — Medium/High.**

A pool configured with `SwapAllowlistExtension` is a curated pool: the admin intends to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses). When the router is allowlisted — the necessary step for any allowlisted user to use the standard periphery — the guard becomes completely ineffective. Any address can execute swaps on the curated pool, draining LP value at oracle-derived prices that were only intended to be accessible to trusted counterparties. The pool's core invariant ("only allowlisted addresses may swap") is broken for every swap routed through the standard periphery.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. A pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to use the standard router must allowlist the router itself. This is the obvious and expected operational step. Once taken, the bypass is unconditional and requires no special privileges — any EOA can call `exactInputSingle` on the router and trade on the curated pool.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary contract. Two approaches:

1. **Pass the original user through the router.** Have `MetricOmmSimpleRouter` store `msg.sender` in transient storage (it already does this for the payer context) and expose it so the pool can forward it as `sender` to extensions. This requires a protocol-level convention.

2. **Check `recipient` instead of `sender` in `SwapAllowlistExtension`.** The recipient is the address that receives output tokens and is set to the actual user by the router. This is a simpler fix but changes the semantic of the allowlist from "who initiates" to "who receives."

3. **Allowlist the router separately and require the router to forward the original caller.** Modify the router to pass `msg.sender` as an additional field in `extensionData`, and update the extension to decode and check it when the direct caller is a known router.

The cleanest fix is option 1: the pool should receive the original initiating user as a distinct field from the contract that called `swap()`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
     })
  2. Router calls pool.swap(recipient=bob, ...)
     → msg.sender at pool = router
  3. Pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes — bob receives tokens despite not being allowlisted

Result:
  bob successfully trades on a curated pool that was supposed to exclude him.
  The allowlist is completely bypassed for any user routing through the router.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
