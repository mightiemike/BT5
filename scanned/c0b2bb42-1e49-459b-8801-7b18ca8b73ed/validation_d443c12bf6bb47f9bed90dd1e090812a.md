### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool call. If the pool admin allowlists the router (a necessary step to support router-mediated swaps for any user), every unprivileged user can bypass the individual-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this as the first argument to `IMetricOmmExtensions.beforeSwap`:

```solidity
// ExtensionCalling.sol
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(recipient, ...)` directly. The pool sees `msg.sender = router`, so `sender = router` is what the extension checks — not the actual end-user:

```solidity
// MetricOmmSimpleRouter.sol
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

The actual user's address (`msg.sender` of the router call) is stored only in transient callback context for payment settlement and is never forwarded to the pool or the extension.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner, always the actual user) rather than `sender` (the intermediary):

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The swap allowlist has no equivalent `owner`-style parameter — the pool's `swap()` signature does not carry the originating user's address, so the extension is structurally unable to check it.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of users faces an impossible configuration choice:

1. **Do not allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all; they must call `pool.swap()` directly, breaking the standard user-facing swap flow.
2. **Allowlist the router** → `allowedSwapper[pool][router] = true`, so the extension passes for every user who routes through the router, regardless of whether that user is individually allowlisted. Any unprivileged user can bypass the allowlist by calling any router entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

In scenario 2, a non-allowlisted user can execute swaps on a pool that was explicitly configured to restrict access, draining LP value or executing trades the pool admin intended to block (e.g., during a restricted launch, KYC-gated pool, or emergency access-control period).

---

### Likelihood Explanation

The router is the standard user-facing swap interface. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router, which immediately opens the bypass to all users. The trigger requires no special privileges: any EOA can call `MetricOmmSimpleRouter.exactInputSingle` with a valid pool address and token approval. The bypass is reachable on every pool that has `SwapAllowlistExtension` configured and the router allowlisted.

---

### Recommendation

**Short term:** Pass the originating user's address through the swap path. One approach: add an `originator` field to the pool's `swap()` calldata or `extensionData` convention, and have the router populate it with `msg.sender`. The extension can then decode and check the originator instead of (or in addition to) `sender`.

**Long term:** Redesign `SwapAllowlistExtension.beforeSwap` to check the economically relevant actor — the entity whose tokens are being pulled — rather than the syntactic `msg.sender` of the pool call. This mirrors how `DepositAllowlistExtension` correctly gates by `owner` (the position owner) rather than `sender` (the intermediary).

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowlisted.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use the standard UI.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `_beforeSwap(router, ...)` is dispatched; extension checks `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes successfully on a pool he was never authorized to access.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
