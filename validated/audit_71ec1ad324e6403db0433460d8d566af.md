Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Original User, Allowlist Fully Bypassed via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`, where `sender` is sourced from the pool's own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` mediates the swap, the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][originalUser]`. Any pool admin who allowlists the router (the only way to let legitimate users trade through it) simultaneously opens the gate to every unprivileged address on-chain.

## Finding Description

**Root cause — pool passes its own `msg.sender` as `sender` to the extension:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first argument of the hook payload: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][pool's msg.sender]`: [3](#0-2) 

**Exploit path — router becomes the checked identity:**

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the original user: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (required for any legitimate user to trade through it), the check passes for every caller of the router regardless of their own allowlist status. There is no on-chain mechanism to distinguish "router called by an allowlisted user" from "router called by an attacker."

**Contrast with `DepositAllowlistExtension`:** The deposit path correctly checks `owner` (the position holder, explicitly passed as a separate argument), not `sender` (the payer/caller), so the deposit guard is not affected: [5](#0-4) 

The swap path has no equivalent `owner`-style separation — `sender` is the only identity forwarded, and it collapses to the router address for all router-mediated swaps.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC-verified, institutional, or otherwise vetted addresses can be freely traded against by any unprivileged address by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutput`/`exactOutputSingle`). The attacker pays only gas. LP funds in the restricted pool are exposed to unrestricted swap flow, defeating the entire purpose of the allowlist extension and potentially violating compliance requirements or LP agreements. This constitutes broken core pool functionality — the allowlist invariant is structurally unenforceable for router-mediated swaps.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can immediately exploit this with a single `exactInputSingle` call.
- Pool admins have no on-chain mechanism to distinguish "router called by an allowlisted user" from "router called by an attacker" — the original caller's identity is lost at the pool boundary.
- The router must be allowlisted for any legitimate user to trade through it, making the bypass condition a natural consequence of normal pool administration.

## Recommendation

The pool must forward the original initiator's address as `sender` rather than its own `msg.sender`. Two viable approaches:

1. **Router passes original user via `extensionData`**: The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension` decodes and verifies it. This requires the extension to trust the router, reintroducing a trust assumption, and requires the extension to be aware of the router's encoding format.

2. **Pool exposes a `swapWithSender` entry-point** (preferred): Add a pool-level function that accepts an explicit `sender` address, callable only by factory-registered periphery contracts. The extension then checks the explicit sender. This mirrors how `addLiquidity` separates `msg.sender` (payer) from `owner` (position holder).

3. **Document that the router must never be allowlisted**: Allowlisted users must call the pool directly. This is a severe UX restriction and is not enforceable on-chain.

## Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists alice and the router (so alice can use the router).
extension.setAllowedToSwap(address(pool), alice, true);
extension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) bypasses the guard via the router.
vm.startPrank(attacker); // attacker != alice, not in allowlist
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000_000,
        amountOutMinimum: 0,
        recipient:        attacker,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// pool.swap() is called with msg.sender = router.
// _beforeSwap(router, ...) is dispatched.
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → no revert.
// Attacker traded on a pool they were never authorized to access.
vm.stopPrank();
```

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
