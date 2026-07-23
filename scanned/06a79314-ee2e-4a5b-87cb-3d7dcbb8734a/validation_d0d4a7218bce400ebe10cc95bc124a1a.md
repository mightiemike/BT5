### Title
`SwapAllowlistExtension` checks router address as swapper instead of actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address that called `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. A pool admin who allowlists the router so that legitimate users can reach the pool through the standard periphery path inadvertently opens the allowlist to every address on-chain.

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and uses it as the identity to check against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by `MetricOmmPool.swap`, which passes `msg.sender` — the immediate caller of the pool:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

So `msg.sender` from the pool's perspective is the router contract, not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router at all — core swap path broken |
| Yes | Every address on-chain can bypass the allowlist via the router |

The actual user's address is stored in the router's transient storage (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, ...)`) for payment purposes, but it is never forwarded to the extension layer.

### Impact Explanation

A curated pool that relies on `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that protection entirely once the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the pool and the extension will pass because it sees the allowlisted router as the swapper. This is a direct policy bypass on a live user flow with fund-impacting consequences: unauthorized parties can drain pool liquidity at oracle-derived prices that the pool admin intended to reserve for vetted counterparties.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap entry point for end users. A pool admin who wants allowlisted users to be able to trade through the standard UI/router will naturally allowlist the router address. The misconfiguration is not obvious from the extension's interface or documentation ("Gates `swap` by swapper address, per pool"), and no warning is emitted. The trigger is a routine, semi-trusted admin action with no malicious intent required.

### Recommendation

The extension must be able to identify the true economic actor, not the intermediary. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for the extension to decode and verify. The extension validates that the encoded address matches a signed or trusted claim.
2. **Separate payer from sender in the hook signature**: Add a `payer` field to the `beforeSwap` hook that the pool populates from the callback context (already stored in transient storage by the router), so the extension can check the actual token source rather than the call-chain intermediary.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the LP position owner explicitly passed by the caller) rather than `sender`.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — so Alice can use the standard router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite never being allowlisted. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
