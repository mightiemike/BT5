Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` evaluates router address instead of originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, so the extension evaluates the router's allowlist status rather than the originating user's. This allows any non-allowlisted user to bypass curated-pool access control by routing through the public router, or locks out allowlisted users if the router itself is not allowlisted.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the caller of the extension) and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. No mechanism exists anywhere in the call path to unwrap or recover the originating user's address.

## Impact Explanation
**Allowlist bypass (critical path):** If the pool admin allowlists the router (the natural configuration for normal UX), every non-allowlisted address can bypass the curated-pool restriction by routing through `MetricOmmSimpleRouter`. The allowlist provides zero protection against router-mediated swaps, allowing unauthorized trading on pools that may carry privileged pricing or restricted LP positions.

**Legitimate-user lockout (secondary path):** If the router is not allowlisted, every allowlisted user who swaps through the router is rejected, rendering the primary supported swap entrypoint unusable for allowlisted pools. Both outcomes are fund-impacting and constitute broken core pool functionality and an admin-boundary break.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface. No special privileges, flash loans, or multi-transaction setup are required — a single `exactInputSingle` call suffices. Any user who discovers the allowlist can trivially exploit this by routing through the router instead of calling the pool directly.

## Recommendation
The extension must gate the originating user, not the intermediary. Two sound approaches:

1. **Pass the real user through `extensionData`:** Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it, with the pool verifying the router is a trusted forwarder.
2. **Check `recipient` as the economic actor:** For swap allowlists, the recipient is the address that receives value; gate on `recipient` rather than `sender` when the pool is accessed through a trusted router.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Allowlist MetricOmmSimpleRouter via setAllowedToSwap(pool, router, true).
  - Do NOT allowlist attacker EOA.

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient=attacker, ...) → msg.sender inside pool = router
  3. _beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true → swap proceeds
  5. Attacker receives output tokens despite not being on the allowlist.

Expected: revert NotAllowedToSwap()
Actual:   swap executes successfully
``` [5](#0-4)

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
