### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the **router contract**, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the gate to every user on the network, completely defeating the per-address allowlist.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as `sender` to every `beforeSwap` extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

Therefore, when any user routes through the router, the allowlist check evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router has only one option: add the router address to the allowlist. The moment they do, `allowedSwapper[pool][router] == true` for every swap that arrives through the router, regardless of who the actual end user is. The per-user gate is completely bypassed.

The asymmetry with `DepositAllowlistExtension` makes this worse: the deposit extension correctly gates on `owner` (the position owner, which is the economically relevant identity), while the swap extension gates on `sender` (the calling contract, which is the router): [5](#0-4) 

---

### Impact Explanation

Any user can bypass a pool's swap allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) on a pool whose admin has allowlisted the router. The allowlist is the only on-chain mechanism restricting who may swap in such pools. Once bypassed, every address on the network can execute swaps, draining LP-owed output tokens at oracle-derived prices and breaking the admin-configured access boundary. This is an admin-boundary break with direct swap execution by unprivileged callers.

---

### Likelihood Explanation

The likelihood is **medium**. Allowlisting the router is the natural, expected configuration for any pool that wants its approved users to access the standard periphery UX. The pool admin has no other way to enable router-mediated swaps for allowlisted users without also opening the gate to everyone. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard call to the public router.

---

### Recommendation

The extension must identify the **end user**, not the calling contract. Two viable approaches:

1. **Pass the payer/originator through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention.
2. **Check `tx.origin` as a fallback for known router callers**: If `sender` is a known router, fall back to `tx.origin`. This is safe only when the router is the sole trusted intermediary and `tx.origin` cannot be spoofed by a malicious contract in the call chain.
3. **Document and enforce direct-only swaps**: If the allowlist is intended to gate individual addresses, document that router-mediated swaps are incompatible and revert when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only approved swapper
  allowedSwapper[P][router] = true  // admin adds router so alice can use the UI

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, ..., extensionData)   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, bob receives output tokens

Result:
  bob successfully swaps in a pool he is not allowlisted for.
  The per-user allowlist is completely bypassed.
``` [6](#0-5) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-170)
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
