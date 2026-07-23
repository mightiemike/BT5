Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user when swaps are routed through `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool at the time `pool.swap()` is called. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the pool passes the router address as `sender` to the extension. The allowlist check therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, either silently opening the allowlist to all router users (if the router is allowlisted) or permanently blocking allowlisted users from using the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly, making the router the `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore passes `address(router)` as `sender` to the extension. The same applies to `exactOutputSingle` (L136-137) and all hops of `exactInput` (L104-112) and `exactOutput` (L165-181). In multi-hop `exactInput`, intermediate hops use `address(this)` (the router) as the payer, so every hop beyond the first also presents the router as `sender`.

`DepositAllowlistExtension` avoids this problem by checking `owner` (the second argument, explicitly representing the LP position owner) rather than `sender` (the direct caller). No equivalent originating-user field exists on the swap interface — `beforeSwap` receives only `sender` and `recipient`, and neither carries the end user's identity when routed.

## Impact Explanation

**Scenario A — Allowlist bypass (Critical/High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses. To allow those users to use the router, the admin adds the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Because `MetricOmmSimpleRouter` is a public, permissionless contract, any address can call `exactInputSingle` through it. The extension sees `sender = router` and passes the check for every caller, completely defeating per-user curation. All user principal flowing through the pool is accessible to unapproved swappers — direct loss of curation policy above Sherlock thresholds.

**Scenario B — Broken core swap functionality (High):** A pool admin allowlists individual user addresses for direct `pool.swap()` calls. Those users cannot swap through the router because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`. The router — the primary production entry point — is permanently broken for all allowlisted users on that pool, constituting broken core swap functionality causing unusable swap flows.

## Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` is affected. This is the expected production configuration: `SwapAllowlistExtension` is a production extension in the periphery, and `MetricOmmSimpleRouter` is the primary production router. The trigger requires no privileged access — any public user calling the router reaches the vulnerable path. No special preconditions beyond normal pool setup are required.

## Recommendation

The swap interface must carry the originating user's identity to the extension. Two approaches:

1. **Preferred:** Add a dedicated `originator` field to the `pool.swap()` interface that the router populates with `msg.sender` before calling the pool. The pool forwards this to `_beforeSwap` and the extension checks `allowedSwapper[pool][originator]` instead of `allowedSwapper[pool][sender]`. This mirrors how `addLiquidity` already separates `sender` (direct caller) from `owner` (position owner).

2. **Alternative:** Have the router encode `msg.sender` in `extensionData` and have the extension decode and verify it. This introduces trust assumptions (the extension must trust the router's encoding) and is less clean than option 1.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is the intended curated user)
  - allowedSwapper[pool][router] = false (router is not explicitly allowlisted)

Scenario A — bypass:
  1. Admin sets allowedSwapper[pool][router] = true to let alice use the router
  2. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  3. Router calls pool.swap(recipient=bob, ...)  →  pool sees msg.sender = router
  4. Pool calls _beforeSwap(sender=router, ...)  →  extension receives sender=router
  5. allowedSwapper[pool][router] == true  →  check passes
  6. Bob's swap executes despite not being on the allowlist

Scenario B — broken functionality:
  1. Admin sets allowedSwapper[pool][alice] = true (no router entry)
  2. Alice calls router.exactInputSingle({pool: pool, ...})
  3. Router calls pool.swap(recipient=alice, ...)  →  pool sees msg.sender = router
  4. Pool calls _beforeSwap(sender=router, ...)  →  extension receives sender=router
  5. allowedSwapper[pool][router] == false  →  revert NotAllowedToSwap
  6. Alice cannot use the router despite being allowlisted

Foundry test plan:
  - Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter
  - setAllowedToSwap(pool, router, true); call exactInputSingle as an unlisted address
  - Assert swap succeeds (demonstrates bypass)
  - Reset: setAllowedToSwap(pool, router, false); setAllowedToSwap(pool, alice, true)
  - Call exactInputSingle as alice; assert revert NotAllowedToSwap (demonstrates broken functionality)
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
