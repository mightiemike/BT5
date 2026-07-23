Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Original EOA, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract address, not the original EOA. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every non-allowlisted user can bypass the per-user allowlist by calling the router instead of the pool directly.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` encodes this value as the first positional argument forwarded to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks that first argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool address and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

At the pool, `msg.sender` is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalEOA]`. The pool admin faces an irresolvable dilemma: not allowlisting the router blocks all router-mediated swaps; allowlisting the router opens the pool to every unprivileged user. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` for KYC compliance, LP-strategy access control, or regulatory gating, and allowlists the router to support standard UX, inadvertently opens the pool to every unprivileged user. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInputSingle` with the curated pool as the target and execute a swap that the allowlist was supposed to block. This is an admin-boundary break: an unprivileged path bypasses a factory-configured access control, allowing unauthorized actors to trade against LP capital in a pool they were explicitly excluded from.

## Likelihood Explanation
The router is the standard periphery swap path documented and expected by integrators. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router, which immediately opens the bypass to all users. The trigger requires only a standard `exactInputSingle` call — no special permissions, no flash loans, no multi-step setup. The same bypass applies to `exactInput` and `exactOutputSingle` via the same router call pattern.

## Recommendation
The extension must recover the original EOA rather than trusting the `sender` argument when the immediate caller is a known periphery contract. Two sound approaches:

1. **Pass the original initiator through the extension payload**: The router encodes `msg.sender` (the EOA) into `extensionData`; the extension reads and verifies it. This requires a coordinated convention between router and extension.
2. **Check the original transaction origin**: Replace `sender` with `tx.origin` inside the extension for the allowlist lookup. This is safe here because the extension is only checking identity for access control (not for payment), and `tx.origin` correctly identifies the EOA that initiated the transaction regardless of intermediary contracts.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension (EXTENSION_1, beforeSwap order = 1)
  - Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true)
    → alice is allowlisted
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    → router is allowlisted so alice can use it
  - charlie is NOT allowlisted

Attack:
  1. charlie calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient, ...) — msg.sender at pool = router
  3. pool calls _beforeSwap(router, recipient, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes for charlie despite charlie not being on the allowlist

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
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
