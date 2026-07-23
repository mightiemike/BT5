### Title
`SwapAllowlistExtension` gates on the intermediary caller (`sender = msg.sender` of `pool.swap()`) rather than the actual end-user, so allowlisting the router silently opens the pool to every router user - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is documented as "Gates `swap` by swapper address, per pool." Its `beforeSwap` hook receives `sender` — which the pool sets to `msg.sender` of the `pool.swap()` call — and checks that address against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the **router contract**, not the end-user. A pool admin who allowlists the router (a natural operational step) therefore grants every router user unrestricted swap access, defeating the per-user gate entirely.

---

### Finding Description

**Pool → Extension call chain**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← caller of pool.swap(), NOT the end-user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...))
);
```

**The allowlist check**

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender /* pool */][sender /* caller of swap */]`:

```solidity
// SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

**Router path**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

`msg.sender` of that `pool.swap()` call is the **router**, so `sender` inside `beforeSwap` is the router address. The extension checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router, the check passes for **every user** who calls through the router, regardless of whether that user is individually allowlisted.

**Contrast with `DepositAllowlistExtension`**

The deposit-side extension correctly checks `owner` — the actual position beneficiary — not `sender` (the operator/router):

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The pool's own NatSpec explicitly documents the operator pattern for deposits: "`msg.sender` pays but need not equal `owner`." No equivalent end-user identity is threaded through the swap path, so the swap allowlist has no way to distinguish the router from the actual trader.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC'd institutions, whitelisted market-makers) is silently opened to the entire public the moment the pool admin allowlists the router. Any user can call `MetricOmmSimpleRouter.exactInputSingle / exactOutputSingle / exactInput / exactOutput` and the extension will pass them through. Unauthorized traders can drain pool liquidity at oracle-anchored prices, causing direct LP principal loss from a pool that was explicitly configured to prevent open access.

**Severity: Medium** — direct LP asset loss; requires the pool admin to have allowlisted the router (a natural operational step for any pool that wants router support), but the admin has no on-chain signal that doing so voids the per-user gate.

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` as a `beforeSwap` hook and (b) allowlists the router is vulnerable. Allowlisting the router is the standard way to enable router-based swaps; the pool admin has no reason to suspect it collapses the per-user allowlist. The inconsistency with `DepositAllowlistExtension` (which checks `owner`, not `sender`) makes the mistake easy to miss during review.

---

### Recommendation

Thread the actual end-user identity through the swap allowlist check. Two options:

1. **Add a `recipient` check**: gate on `recipient` (the address receiving output tokens) instead of `sender`. This is already available in `beforeSwap` and represents the economic beneficiary of the swap.

2. **Require `sender == recipient` for allowlisted pools**: document and enforce that allowlisted pools must be called directly (not through a router that separates payer from recipient).

3. **Align with the deposit pattern**: if the protocol wants a true per-user swap gate, the pool must pass the end-user identity (analogous to `owner` in `addLiquidity`) as a distinct parameter through the extension interface.

---

### Proof of Concept

```
1. Pool admin creates pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls extension.setAllowedToSwap(pool, router, true)
   — intending to "enable router support."
3. Unauthorized user (not individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
4. Router calls pool.swap(...); msg.sender = router.
5. Extension checks allowedSwapper[pool][router] == true → passes.
6. Unauthorized user receives output tokens; LP suffers loss.
7. Direct call pool.swap() by the same unauthorized user would revert
   (allowedSwapper[pool][user] == false), confirming the bypass is
   router-specific.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```
