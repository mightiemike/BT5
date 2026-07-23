Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the real caller, allowing any user to bypass the per-pool swap allowlist when the router is allowlisted — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives the `sender` argument that `MetricOmmPool.swap` populates with its own `msg.sender`. When a user enters through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the real user. If the pool admin allowlists the router — a natural step to let approved users reach the pool via the standard periphery — every unprivileged user can bypass the allowlist by routing through the same public contract. Additionally, `SwapAllowlistExtension.beforeSwap` drops the `onlyPool` modifier present in `BaseMetricExtension`, allowing any address to call it directly.

## Finding Description

**Root cause — identity mismatch:**

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that `sender` as the identity to check against the allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same applies to `exactInput` (multi-hop): [5](#0-4) 

And to the recursive `exactOutput` callback path: [6](#0-5) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][real_user]`. There is no mechanism in the extension to distinguish which end-user is behind the router call.

**Secondary issue — missing `onlyPool` modifier:**

`BaseMetricExtension.beforeSwap` declares the `onlyPool` modifier: [7](#0-6) 

`SwapAllowlistExtension.beforeSwap` overrides this function without the `onlyPool` modifier, making it callable by any address: [8](#0-7) 

When called directly by an arbitrary address, `msg.sender` is that address, and `allowedSwapper[arbitrary_address][sender]` is checked — which is always false unless that address has been configured as a pool, so in practice this path reverts. However, the missing guard is a correctness defect that violates the base contract's security invariant.

## Impact Explanation

A curated pool with `SwapAllowlistExtension` is explicitly designed to restrict trading to a named set of counterparties. Once the router is allowlisted (required for any approved user to use the standard periphery), the guard fails open for every public user: any address can call `exactInputSingle`, `exactInput`, or `exactOutput` and trade in the pool as if they were approved. This is a direct admin-boundary break — an unprivileged path (the public router) defeats a configured access-control extension — and constitutes broken core pool functionality for the allowlist use-case. The wrong value is `allowedSwapper[pool][router]` being evaluated in place of `allowedSwapper[pool][real_user]`, causing the extension decision to be `true` for all router callers instead of only approved ones.

## Likelihood Explanation

Medium. A pool admin who wants approved users to reach the pool through the standard periphery will naturally allowlist the router. The admin is unlikely to realize that doing so opens the gate to all users, because `isAllowedToSwap` and `setAllowedToSwap` both operate on individual addresses and give no indication that the router is a special case. The trigger requires no privileged escalation beyond the admin's own routine configuration step and is repeatable by any address at any time once the router is allowlisted.

## Recommendation

`SwapAllowlistExtension` must not rely on the `sender` argument for identity when that argument can be an intermediary contract. Two viable approaches:

1. **Require the router to attest the real caller in `extensionData`** — the extension decodes the original `msg.sender` from the payload and verifies it against the allowlist. The router must be trusted to populate this field honestly (it already stores the real payer in transient storage via `_setNextCallbackContext`).
2. **Gate on `recipient` instead of `sender`** — if the pool's design guarantees that the economic beneficiary is always `recipient`, the allowlist can check that address instead. This must be validated against the full swap interface.

Additionally, restore the `onlyPool` modifier on `SwapAllowlistExtension.beforeSwap` to match the base class invariant.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, userA, true)   // approve a real user
3. Admin calls setAllowedToSwap(pool, router, true)  // allow router so userA can use periphery
4. Attacker (userB, not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      <curated pool>,
           recipient: userB,
           ...
       })
5. Router calls pool.swap(userB, ...) — pool's msg.sender == router.
6. Pool calls _beforeSwap(router, userB, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → passes.
8. Swap executes. userB trades in the curated pool without being on the allowlist.
```

Foundry test outline: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)` without adding `userB`, then `vm.prank(userB)` call `exactInputSingle` and assert the swap succeeds rather than reverting with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
