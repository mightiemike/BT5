Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end-user identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` enforces the allowlist against the `sender` argument, which `MetricOmmPool.swap()` always sets to its own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]` — not the end user's address. An admin who allowlists the router to enable legitimate router-mediated swaps for authorized users inadvertently opens the pool to every user, defeating the allowlist entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. `MetricOmmPool.swap()` always passes its own `msg.sender` as that argument:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
)
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unmodified to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [4](#0-3) 

Therefore, the extension receives `sender = router` and evaluates `allowedSwapper[pool][router]`. If the router is allowlisted (a necessary step for any authorized user to use it), every user — authorized or not — can call the router and pass the check. There is no configuration that simultaneously allows authorized users to route through `MetricOmmSimpleRouter` and blocks unauthorized users from doing the same.

## Impact Explanation

The swap allowlist is the sole mechanism preventing unauthorized counterparties from trading against a restricted pool's liquidity. Bypassing it allows unauthorized users to execute swaps at oracle-anchored prices against a pool that was deliberately restricted. This constitutes a direct loss of LP principal, as LPs in a restricted pool accepted liquidity risk only against a known set of counterparties. This matches the "Allowlist path" Smart Audit Pivot: allowlist checks must cover the exact actor/action intended and cannot be bypassed through the router.

## Likelihood Explanation

Medium. The bypass requires the admin to allowlist the router — a natural and necessary configuration step if any authorized user is expected to use the router. The admin has no way to know this step opens the pool to all users; the allowlist API (`setAllowedToSwap`) presents it as a per-address control. Once the router is allowlisted for even one authorized user, the bypass is permanently available to everyone with no further preconditions.

## Recommendation

The extension must verify the end user's identity, not the intermediary's. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes the originating `msg.sender` into `extensionData`; the extension decodes and verifies it against the allowlist. The pool already forwards `extensionData` unmodified to every hook via `ExtensionCalling._beforeSwap`.
2. **Separate router-level allowlist**: Deploy a router wrapper that enforces its own per-user allowlist before calling `pool.swap()`, and allowlist only that wrapper in the extension.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Admin calls `extension.setAllowedToSwap(pool, alice, true)` — Alice is authorized.
3. Admin calls `extension.setAllowedToSwap(pool, router, true)` — necessary so Alice can use `MetricOmmSimpleRouter`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes against the restricted pool, bypassing the allowlist entirely.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
