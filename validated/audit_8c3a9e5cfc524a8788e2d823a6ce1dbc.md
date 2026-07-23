Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is the pool's `msg.sender` — the router contract — not the originating EOA. When a pool admin allowlists the router so that individually-allowlisted users can swap via `MetricOmmSimpleRouter`, every unprivileged user gains the same access, completely defeating the per-user curation the extension is designed to enforce.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← pool's msg.sender = the router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes that value directly into the call to the extension:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the originating EOA:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

A pool admin who wants allowlisted users to use the standard router must allowlist the router address. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, including users who were never individually allowlisted. The admin faces an inescapable dilemma: not allowlisting the router breaks the standard swap flow for legitimate users; allowlisting it opens the pool to all users.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps on the pool via the router, enabling unauthorized arbitrage or trades the pool admin explicitly intended to block. This is a direct bypass of an admin-configured access-control guard with fund-impacting consequences: LP value can be drained through arbitrage by actors the pool was designed to exclude.

## Likelihood Explanation
The router is the canonical, documented periphery entry point for swaps. Any pool using `SwapAllowlistExtension` that also wants allowlisted users to swap via the router must allowlist the router — the bypass is a natural consequence of normal operational setup. No privileged access, special tokens, or unusual preconditions are required beyond the router being allowlisted, which is the expected production configuration.

## Recommendation
Pass the originating user's address through the swap call so the extension can gate the correct actor. Two concrete options:

1. **Pool-level fix**: Have the pool record the original `msg.sender` in transient storage before calling extensions, and expose it as a `swapInitiator()` view so extensions can read the real user instead of the router.
2. **Extension-level fix**: Change `SwapAllowlistExtension.beforeSwap` to ignore the `sender` argument and instead require callers to supply the real user address inside `extensionData`, then verify it against the allowlist. The router would forward the user-supplied bytes unchanged.

Note: `DepositAllowlistExtension.beforeAddLiquidity` does not share this flaw because it gates `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order = extension 1)
  allowedSwapper[pool][alice] = true          // alice is individually allowlisted
  allowedSwapper[pool][router] = true         // router allowlisted so alice can use it
  bob = arbitrary EOA, NOT in allowedSwapper

Attack:
  1. bob calls router.exactInputSingle({pool: pool, ..., recipient: bob})
  2. router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
     → pool's msg.sender = router
  3. pool calls _beforeSwap(sender=router, recipient=bob, ...)
  4. ExtensionCalling encodes beforeSwap(router, bob, ...)
  5. SwapAllowlistExtension checks allowedSwapper[pool][router] → TRUE
  6. Swap executes for bob despite bob never being allowlisted.

Result: bob trades on a curated pool, bypassing the per-user allowlist entirely.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
