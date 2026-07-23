Looking at the Metric OMM codebase, I need to find an analog to the ClaggBaseAdapter bug class: a configured guard that is bypassed or misapplied with fund-impacting consequences. Let me trace the swap allowlist path carefully.

The `SwapAllowlistExtension.beforeSwap` checks `sender` (the direct caller of `pool.swap`):

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)`, the pool's `msg.sender` is the router. The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`, which forwards it to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The `DepositAllowlistExtension` correctly gates on `owner` (the position owner, not the payer), but the `SwapAllowlistExtension` gates on `sender` which collapses to the router address for all router-mediated swaps.

---

### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the end user, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. A pool admin who allowlists the router (the natural step to enable router-mediated swaps for their allowlisted users) inadvertently grants every user on-chain the ability to bypass the allowlist entirely.

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool's `_beforeSwap` dispatcher:

```solidity
// ExtensionCalling.sol
function _beforeSwap(address sender, address recipient, ...) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
    );
}
```

The pool sets `sender = msg.sender` of the `swap` call. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making `msg.sender = router`. The extension therefore evaluates:

```solidity
allowedSwapper[pool][router]   // NOT allowedSwapper[pool][end_user]
```

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the guard passes for **every** caller of the router — the end user's identity is never consulted. The two available configurations are both broken:

| Admin choice | Effect |
|---|---|
| Do not allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | Every user on-chain can bypass the allowlist via the router |

There is no configuration that achieves the intended goal: allowing specific end users to swap through the router while blocking others.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner), which the `MetricOmmPoolLiquidityAdder` correctly forwards as the end user's address. The swap path has no equivalent forwarding mechanism — the pool's `swap` signature has no `sender` parameter; it always uses `msg.sender`.

### Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., a private institutional pool, a KYC-gated pool, or a pool with favorable pricing for specific LPs) loses its access control entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and execute swaps against the pool, draining LP-owned liquidity at oracle-derived prices that were intended only for allowlisted counterparties. This is a direct loss of LP principal through unauthorized swap execution — matching the "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path" impact categories.

### Likelihood Explanation
The likelihood is medium-high. Any pool admin who deploys a swap-allowlisted pool and also wants their allowlisted users to benefit from the router's slippage protection, multi-hop routing, or deadline checks will naturally allowlist the router. The documentation for `SwapAllowlistExtension` does not warn that allowlisting the router collapses the per-user gate. The mistake is predictable and requires no malicious intent — only the routine act of enabling router access.

### Recommendation
The extension must verify the end user's identity, not the immediate caller. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to populate the field honestly, which is acceptable since the router is a known, audited contract.

2. **Check `recipient` instead of `sender` for router-mediated swaps**: Less clean but avoids the forwarding requirement.

3. **Preferred — add a `trustedForwarder` registry**: The extension maintains a mapping of trusted forwarders (e.g., the router). When `sender` is a trusted forwarder, the extension decodes the real user from `extensionData` and checks that address instead.

The `DepositAllowlistExtension` pattern (gating on `owner`, which the periphery correctly forwards) should be the model.

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured. Pool admin allowlists `alice` and the router address.
2. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
3. The router calls `pool.swap(bob_recipient, ...)`. Pool sets `sender = router`.
4. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. `bob` successfully swaps in a pool he is not authorized to access.

Direct call by `bob` to `pool.swap(...)` would set `sender = bob` → `allowedSwapper[pool][bob]` → `false` → revert. The router path bypasses the check entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L121-125)
```text
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
